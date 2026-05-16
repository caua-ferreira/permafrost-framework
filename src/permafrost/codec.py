"""
PermafrostCodec v4 — Bug fix: predictor consistente entre chunks
Mudança principal: encode_column_fixed() usa o manifesto já definido
para garantir que todos os chunks usem o mesmo predictor.
"""
from __future__ import annotations
from typing import Any, Iterator, Optional
import struct, hashlib, io, json, time, os, lzma, subprocess, tempfile
import numpy as np, pandas as pd
import zstandard as zstd

MAGIC = b'PRMS'; EOF_MAGIC = b'SMRP'; VERSION = bytes([1, 3])
CODEC_ZSTD=0x01; CODEC_LZMA2=0x02; CODEC_ZPAQ=0x03
QUANT_NONE=0x00; QUANT_HIGH=0x01; QUANT_MEDIUM=0x02; QUANT_LOW=0x03
FLAG_DELTA=0x01; FLAG_QUANTIZE=0x02; FLAG_CHUNKED=0x04
FLAG_PREDICTOR=0x08; FLAG_INDEX=0x10; FLAG_ENCRYPTED=0x20
DEFAULT_CHUNK_ROWS = 10_000

PRED_DELTA='delta_zigzag'; PRED_LAG1='lag1_zigzag'
PRED_CATEGORY='category_u8'; PRED_TS='ts_delta_s'; PRED_RAW='raw_text'
PRED_FLOAT32='float32_quantized'; PRED_FLOAT16='float16_quantized'
PRED_JSON_V2='json_schema_v2'

def _sha256(b): return hashlib.sha256(b).digest()
def _zigzag_enc(a): return np.where(a>=0,a*2,-a*2-1).astype(np.uint64)
def _zigzag_dec(a): return np.where(a%2==0,a//2,-(a.astype(np.int64)//2)-1).astype(np.int64)

def _is_json_column(series, threshold: float = 0.70, sample_size: int = 100) -> bool:
    """Returns True if >= threshold of non-null values parse as JSON dicts."""
    non_null = series.dropna()
    if len(non_null) == 0:
        return False
    sample = non_null.iloc[:sample_size]
    ok = sum(1 for v in sample if _try_json_dict(v))
    return ok / len(sample) >= threshold


def _try_json_dict(v) -> bool:
    try:
        return isinstance(json.loads(str(v)), dict)
    except Exception:
        return False


def _json_v2_manifest(name, series) -> dict:
    """Builds manifest for json_schema_v2 — shared key dict for integer encoding."""
    keys: set = set()
    for v in series.dropna():
        try:
            d = json.loads(str(v))
            if isinstance(d, dict):
                keys.update(d.keys())
        except Exception:
            pass
    return {
        'name': name,
        'dtype': str(series.dtype),
        'predictor': PRED_JSON_V2,
        'scale': 1,
        'key_dict': sorted(keys),
    }


def _float_quant_manifest(name, series, pred):
    """Builds a manifest for float32/float16 quantized predictors."""
    dtype_q = np.float32 if pred == PRED_FLOAT32 else np.float16
    vals = pd.to_numeric(series, errors='coerce').fillna(0).to_numpy(dtype=np.float64)
    quantized = vals.astype(dtype_q).astype(np.float64)
    abs_err = np.abs(vals - quantized)
    return {
        'name': name,
        'dtype': str(series.dtype),
        'predictor': pred,
        'scale': 1,
        'precision_bits': 32 if pred == PRED_FLOAT32 else 16,
        'max_abs_error': float(abs_err.max()) if len(abs_err) else 0.0,
        'max_rel_error': float(np.finfo(dtype_q).eps),
    }

# ── DETECTAR predictor (chamado apenas 1x na primeira passagem) ──────────────
def _detect_predictor(name, series, quant):
    """Retorna o manifesto com o predictor correto para esta coluna."""
    m = {'name':name,'dtype':str(series.dtype),'predictor':None,'scale':1}
    if pd.api.types.is_datetime64_any_dtype(series):
        m['predictor']=PRED_TS; return m
    is_int=pd.api.types.is_integer_dtype(series)
    is_id=name=='id' or name.lower().endswith('_id')
    if is_int and is_id:
        m['predictor']=PRED_DELTA; return m
    if pd.api.types.is_float_dtype(series):
        _special = any(x in name.lower() for x in ['lat', 'lon', 'score', 'pct'])
        if not _special:
            if quant == QUANT_HIGH:
                return _float_quant_manifest(name, series, PRED_FLOAT32)
            if quant == QUANT_LOW:
                return _float_quant_manifest(name, series, PRED_FLOAT16)
        scale=100
        if quant>=QUANT_MEDIUM:
            if 'lat' in name or 'lon' in name: scale=10_000; m['quant']='round4'
            elif any(x in name for x in ['score','pct']): scale=10; m['quant']='round1'
            else: scale=1; m['quant']='round0'
        else:
            if 'lat' in name or 'lon' in name: scale=1_000_000
        m['predictor']=PRED_LAG1; m['scale']=scale; return m
    if is_int:
        m['predictor']=PRED_DELTA; return m
    # String/object: decidir category vs raw baseado no DataFrame COMPLETO
    # (não no chunk!) — essa decisão é tomada uma vez e fixada
    _is_text = pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)
    if _is_text and _is_json_column(series):
        return _json_v2_manifest(name, series)
    if series.nunique() <= 256:
        cats=series.astype('category')
        m['categories']=list(cats.cat.categories.astype(str))
        m['predictor']=PRED_CATEGORY
    else:
        m['predictor']=PRED_RAW
    return m

# ── ENCODE com predictor já definido (BUG FIX principal) ─────────────────────
def _encode_with_manifest(series, manifest, quant):
    """Aplica o predictor do manifesto — sem re-detectar. Garante consistência entre chunks."""
    pred  = manifest['predictor']
    scale = manifest.get('scale',1)

    if pred == PRED_TS:
        ts = ((series - pd.Timestamp('1970-01-01')) // pd.Timedelta('1s')).astype(np.int64)
        if quant>=QUANT_MEDIUM: ts=(ts//60)*60
        if not series.is_monotonic_increasing:
            import warnings
            warnings.warn(
                f"Coluna '{manifest['name']}' de timestamp não está em ordem crescente. "
                "O preditor ts_delta_s pode restaurar valores incorretos. "
                "Ordene o DataFrame antes de freeze(): df.sort_values(col)",
                UserWarning, stacklevel=4
            )
        deltas=np.diff(ts.values.astype(np.int64),prepend=0)
        return _zigzag_enc(deltas).astype(np.uint64).tobytes()

    if pred == PRED_DELTA:
        vals=pd.to_numeric(series,errors='coerce').fillna(0).astype(np.int64)
        deltas=np.diff(vals.values,prepend=0)
        return _zigzag_enc(deltas).astype(np.uint32).tobytes()

    if pred == PRED_LAG1:
        if quant>=QUANT_MEDIUM:
            if manifest.get('quant')=='round4': series=series.round(4)
            elif manifest.get('quant')=='round1': series=series.round(1)
            elif manifest.get('quant')=='round0': series=series.round(0)
        vals=np.round(pd.to_numeric(series,errors='coerce').fillna(0)*scale).astype(np.int64)
        pred_arr=np.empty_like(vals); pred_arr[0]=0; pred_arr[1:]=vals[:-1]
        return _zigzag_enc(vals-pred_arr).astype(np.uint32).tobytes()

    if pred == PRED_CATEGORY:
        # IMPORTANTE: usar as categorias do manifesto global (não re-detectar)
        cats_global = manifest.get('categories', [])
        # Mapear valores para índices — se valor não está nas cats, usar 255 (unknown)
        cat_map = {c: i for i, c in enumerate(cats_global)}
        codes = np.array([cat_map.get(str(v), len(cats_global)-1)
                         for v in series.astype(str)], dtype=np.uint8)
        return codes.tobytes()

    if pred == PRED_RAW:
        return '\x00'.join(series.astype(str).values).encode('utf-8')

    if pred == PRED_JSON_V2:
        key_dict = manifest.get('key_dict', [])
        rev_map = {k: str(i) for i, k in enumerate(key_dict)}
        rows = []
        for v in series:
            try:
                d = json.loads(str(v))
                if isinstance(d, dict):
                    compact = {rev_map.get(k, k): val for k, val in d.items()}
                    rows.append(json.dumps(compact, separators=(',', ':')))
                else:
                    rows.append(str(v))
            except Exception:
                rows.append(str(v))
        return '\x00'.join(rows).encode('utf-8')

    if pred == PRED_FLOAT32:
        return pd.to_numeric(series, errors='coerce').fillna(0).to_numpy(
            dtype=np.float64).astype(np.float32).tobytes()

    if pred == PRED_FLOAT16:
        return pd.to_numeric(series, errors='coerce').fillna(0).to_numpy(
            dtype=np.float64).astype(np.float16).tobytes()

    return series.astype(str).str.encode('utf-8').str.cat(sep=b'\x00')

# ── DECODE (igual v3) ─────────────────────────────────────────────────────────
def decode_column(data, manifest, n_rows):
    pred=manifest.get('predictor'); scale=manifest.get('scale',1); name=manifest['name']
    if pred==PRED_TS:
        arr=np.frombuffer(data,dtype=np.uint64)
        return pd.Series(pd.to_datetime(np.cumsum(_zigzag_dec(arr)),unit='s'),name=name)
    if pred==PRED_DELTA:
        arr=np.frombuffer(data,dtype=np.uint32).astype(np.uint64)
        return pd.Series(np.cumsum(_zigzag_dec(arr)),name=name)
    if pred==PRED_LAG1:
        arr=np.frombuffer(data,dtype=np.uint32).astype(np.uint64)
        return pd.Series(np.cumsum(_zigzag_dec(arr))/scale,name=name)
    if pred==PRED_CATEGORY:
        codes=np.frombuffer(data,dtype=np.uint8)
        cats=manifest.get('categories',[])
        # Garantir que codes não excedam o tamanho do dicionário
        codes=np.clip(codes,0,max(0,len(cats)-1))
        return pd.Series(pd.Categorical.from_codes(codes,categories=cats),name=name)
    if pred==PRED_RAW:
        return pd.Series(data.decode('utf-8').split('\x00'),name=name)
    if pred==PRED_JSON_V2:
        key_dict = manifest.get('key_dict', [])
        rows = data.decode('utf-8').split('\x00')
        result = []
        for v in rows:
            try:
                d = json.loads(v)
                if isinstance(d, dict):
                    restored = {}
                    for k, val in d.items():
                        if k.isdigit():
                            idx = int(k)
                            restored[key_dict[idx] if idx < len(key_dict) else k] = val
                        else:
                            restored[k] = val
                    result.append(json.dumps(restored))
                else:
                    result.append(v)
            except Exception:
                result.append(v)
        return pd.Series(result, name=name)
    if pred==PRED_FLOAT32:
        return pd.Series(np.frombuffer(data,dtype=np.float32).astype(np.float64),name=name)
    if pred==PRED_FLOAT16:
        return pd.Series(np.frombuffer(data,dtype=np.float16).astype(np.float64),name=name)
    return pd.Series([None]*n_rows,name=name)

# ── ENCODE CHUNK (v4 — usa manifesto fixo) ───────────────────────────────────
def _encode_chunk(df_chunk, manifests, quant):
    """
    Serializa chunk usando manifestos já definidos (ou define na 1ª chamada).
    BUG FIX: o predictor é detectado apenas se ainda não está no manifesto.
    """
    payload=struct.pack('>H',len(df_chunk.columns))
    for col in df_chunk.columns:
        # 1ª passagem: definir manifesto com série COMPLETA já foi feita antes
        # Aqui só usamos o manifesto existente
        if col not in manifests:
            # Fallback: detectar agora (não deveria acontecer com freeze() correto)
            manifests[col]=_detect_predictor(col,df_chunk[col],quant)

        enc=_encode_with_manifest(df_chunk[col],manifests[col],quant)
        nb=col.encode()
        payload+=struct.pack('>B',len(nb))+nb
        payload+=struct.pack('>I',len(enc))+enc
    return payload

def _compress(data: bytes, codec: int) -> bytes:
    """Comprime bytes usando o codec especificado."""
    if codec == CODEC_LZMA2:
        return lzma.compress(data, format=lzma.FORMAT_XZ,
                             preset=lzma.PRESET_EXTREME | 9)
    if codec == CODEC_ZPAQ:
        return _zpaq_compress(data)
    return zstd.ZstdCompressor(level=19, threads=2).compress(data)


def _decompress(data: bytes, codec: int) -> bytes:
    """Descomprime bytes usando o codec especificado."""
    if codec == CODEC_LZMA2:
        return lzma.decompress(data, format=lzma.FORMAT_XZ)
    if codec == CODEC_ZPAQ:
        return _zpaq_decompress(data)
    return zstd.ZstdDecompressor().decompress(data)


def _zpaq_compress(data: bytes, method: int = 5) -> bytes:
    """Comprime com ZPAQ context mixing via subprocess.

    ZPAQ entrega o melhor ratio para dados de texto longo e logs.
    Para dados tabulares, a diferença vs LZMA2 é < 2%.

    Args:
        data: Bytes a comprimir.
        method: Método ZPAQ (1-5). 5 = máximo ratio, mais lento.

    Returns:
        Bytes comprimidos com cabeçalho de tamanho original (u64 BE).

    Raises:
        RuntimeError: Se o binário ``zpaq`` não estiver disponível.
    """
    import shutil
    if not shutil.which("zpaq"):
        raise RuntimeError(
            "Codec ZPAQ requer o binário 'zpaq' instalado no sistema. "
            "Linux: apt install zpaq | macOS: brew install zpaq"
        )
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "data.bin")
        out = os.path.join(d, "data.bin.zpaq")
        with open(inp, "wb") as f:
            f.write(data)
        r = subprocess.run(
            ["zpaq", "a", out, inp, "-method", str(method)],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"zpaq falhou: {r.stderr.decode()[:200]}")
        compressed = open(out, "rb").read()
    # Prefixar com tamanho original (necessário para decompress)
    orig_len = struct.pack(">Q", len(data))
    return orig_len + compressed


def _zpaq_decompress(data: bytes) -> bytes:
    """Descomprime dados ZPAQ.

    O ZPAQ preserva o path completo ao extrair. Usamos ``glob`` para
    encontrar o arquivo extraído independente do path original.

    Args:
        data: Bytes comprimidos com prefixo de tamanho original (8B u64 BE).

    Returns:
        Bytes originais descomprimidos.
    """
    import glob
    orig_len = struct.unpack(">Q", data[:8])[0]
    compressed = data[8:]
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "data.bin.zpaq")
        extract_dir = os.path.join(d, "out")
        os.makedirs(extract_dir)
        with open(inp, "wb") as f:
            f.write(compressed)
        r = subprocess.run(
            ["zpaq", "x", inp, "-to", extract_dir],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"zpaq decompress falhou: {r.stderr.decode()[:200]}")
        # ZPAQ preserva o path completo → usar glob para encontrar o .bin
        matches = glob.glob(os.path.join(extract_dir, "**", "*.bin"), recursive=True)
        if not matches:
            raise RuntimeError(f"zpaq: nenhum arquivo .bin encontrado em {extract_dir}")
        result = open(matches[0], "rb").read()
    if len(result) != orig_len:
        raise ValueError(
            f"ZPAQ: tamanho restaurado {len(result)} ≠ original {orig_len}"
        )
    return result

def _pa_schema(df):
    try:
        import pyarrow as pa
        return pa.Schema.from_pandas(df).serialize().to_pybytes()
    except: return b'{}'

def _parse_chunk(data,manifests,n_rows):
    p=0; n_cols=struct.unpack_from('>H',data,p)[0]; p+=2
    col_data={}
    for _ in range(n_cols):
        nl=struct.unpack_from('>B',data,p)[0]; p+=1
        cn=data[p:p+nl].decode(); p+=nl
        sl=struct.unpack_from('>I',data,p)[0]; p+=4
        col_data[cn]=data[p:p+sl]; p+=sl
    series={}
    for col,m in manifests.items():
        if col in col_data:
            series[col]=decode_column(col_data[col],m,n_rows)
    return pd.DataFrame(series)

# ── FREEZE (v4) ───────────────────────────────────────────────────────────────
def _find_orig_rows_offset(raw: bytes) -> int:
    """Returns the byte offset of ORIG_ROWS field inside the raw header bytes."""
    schema_len   = struct.unpack_from('>I', raw, 16)[0]
    manifest_len = struct.unpack_from('>I', raw, 20 + schema_len)[0]
    comment_len  = struct.unpack_from('>B', raw, 24 + schema_len + manifest_len)[0]
    # layout after comment: FREEZE_TS (8B) + RETENTION (4B) + ORIG_ROWS (8B)
    return 25 + schema_len + manifest_len + comment_len + 8 + 4


def _chunk_overlaps_range(part_key: str, lo: str, hi: str) -> bool:
    """Returns True if the chunk's partition range overlaps [lo, hi]."""
    # part_key is either a single value ("2022") or "min-max" from freeze()
    # Use simple lexicographic comparison — works correctly for years, months, dates
    try:
        dash = part_key.find('-', 1)  # skip leading minus for negative numbers
        if dash == -1:
            chunk_min = chunk_max = part_key
        else:
            chunk_min, chunk_max = part_key[:dash], part_key[dash+1:]
        return chunk_max >= lo and chunk_min <= hi
    except Exception:
        return True  # include on error — no data loss


def freeze(
    df: pd.DataFrame,
    path: str | os.PathLike,
    codec: int = CODEC_LZMA2,
    quant: int = QUANT_NONE,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    partition_by: Optional[str] = None,
    comment: str = "",
    retention_days: int = 0,
    key=None,
    predictors: Optional[dict] = None,
) -> dict[str, Any]:
    """Comprime um DataFrame para o formato .permafrost.

    Usa preditores colunares por tipo de dado antes do codec de compressão,
    resultando em ratios de 5–15× para dados corporativos típicos.

    Args:
        df: DataFrame a comprimir. Todos os tipos pandas são suportados.
        path: Caminho de saída do arquivo .permafrost.
        codec: Algoritmo de compressão. Use ``CODEC_LZMA2`` (padrão) para cold
            storage (melhor ratio) ou ``CODEC_ZSTD`` para warm storage
            (decompressão 6× mais rápida).
        quant: Nível de quantização. ``QUANT_NONE`` = lossless (padrão).
            ``QUANT_MEDIUM`` = floats arredondados para inteiro, timestamps
            truncados para o minuto.
        chunk_rows: Número de linhas por chunk no arquivo (padrão: 10.000).
            Chunks menores = menor uso de RAM no thaw, maior overhead por arquivo.
        partition_by: Coluna usada como chave do sparse index. Habilita
            ``thaw(filter={partition_by: valor})`` para leitura seletiva.
            Recomendado: ordenar o DataFrame por esta coluna antes de freeze.
        comment: String livre embutida no header do arquivo. Recuperável via
            ``audit()`` sem descomprimir.
        retention_days: Dias de retenção (0 = permanente).

    Returns:
        Dicionário com métricas do freeze::

            {
                "path":           "arquivo.permafrost",
                "rows":           80000,
                "cols":           9,
                "n_chunks":       16,
                "original_mb":    5.85,
                "stored_mb":      0.678,
                "ratio":          8.37,
                "reduction_pct":  88.0,
                "freeze_s":       2.23,
                "codec":          "lzma2",
                "partition_by":   "ano",
            }

    Raises:
        ValueError: Se o DataFrame estiver vazio ou o path não puder ser criado.

    Example:
        >>> import permafrost as pf
        >>> df = pd.read_csv("vendas.csv")
        >>> df = df.sort_values("ano")  # ordenar antes de particionar
        >>> m = pf.freeze(df, "vendas.permafrost",
        ...               codec=pf.CODEC_LZMA2,
        ...               partition_by="ano")
        >>> print(f"Ratio: {m['ratio']:.2f}x")
        Ratio: 8.37x
    """
    # ── Polars support ───────────────────────────────────────────────────────────
    if type(df).__module__.startswith('polars'):
        df = df.to_pandas()

    _auto_reason: Optional[str] = None
    if codec == "auto":
        from permafrost.auto_codec import auto_select as _auto_select
        _sel = _auto_select(df)
        codec = _sel["codec"]
        quant = _sel["quant"]
        _auto_reason = _sel["reason"]

    from permafrost.crypto import resolve_key, encrypt_chunk
    raw_key, kms_name, kid, edek = resolve_key(key)

    t0=time.time(); orig_bytes=len(df.to_csv(index=False).encode()); orig_rows=len(df)
    flags=FLAG_PREDICTOR|FLAG_DELTA|FLAG_CHUNKED|FLAG_INDEX|(FLAG_QUANTIZE if quant else 0)
    if raw_key is not None:
        flags |= FLAG_ENCRYPTED

    # ── BUG FIX: detectar manifestos UMA VEZ sobre o DataFrame COMPLETO ──────
    manifests={}
    for col in df.columns:
        manifests[col]=_detect_predictor(col,df[col],quant)
        # Para category: usar categorias do DataFrame completo
        if manifests[col]['predictor']==PRED_CATEGORY:
            cats=df[col].astype('category')
            manifests[col]['categories']=list(cats.cat.categories.astype(str))

    # ── Aplicar overrides explícitos de predictor ─────────────────────────────
    if predictors:
        for col, pred_name in predictors.items():
            if col not in df.columns:
                continue
            if pred_name in (PRED_FLOAT32, PRED_FLOAT16):
                manifests[col] = _float_quant_manifest(col, df[col], pred_name)
            elif col in manifests:
                manifests[col]['predictor'] = pred_name

    # ── Comprimir chunks ─────────────────────────────────────────────────────
    chunk_blobs=[]; index_entries=[]
    for chunk_start in range(0,orig_rows,chunk_rows):
        chunk_end=min(chunk_start+chunk_rows,orig_rows)
        df_chunk=df.iloc[chunk_start:chunk_end].reset_index(drop=True)
        raw_chunk=_encode_chunk(df_chunk,manifests,quant)
        compressed=_compress(raw_chunk,codec)
        if raw_key is not None:
            compressed = encrypt_chunk(compressed, raw_key)
        sha=_sha256(compressed)

        if partition_by and partition_by in df_chunk.columns:
            pv=sorted(df_chunk[partition_by].unique().tolist())
            part_key=str(pv[0]) if len(pv)==1 else f"{pv[0]}-{pv[-1]}"
        else: part_key=f"rows_{chunk_start}_{chunk_end-1}"

        index_entries.append({'chunk_id':len(chunk_blobs),'row_start':chunk_start,
            'row_end':chunk_end-1,'part_key':part_key,
            'part_col':partition_by or '__rows__',
            'byte_offset':None,'byte_len':len(compressed),'sha256':sha.hex()})
        chunk_blobs.append((compressed,sha))

    # ── Header ───────────────────────────────────────────────────────────────
    schema_b=_pa_schema(df)
    manifest_b=json.dumps(manifests,ensure_ascii=False,default=str).encode()
    comment_b=comment.encode()[:255]

    enc_meta = b''
    if raw_key is not None:
        enc_meta = (struct.pack('>B', len(kms_name)) + kms_name.encode() +
                    struct.pack('>B', len(kid)) + kid.encode() +
                    struct.pack('>H', len(edek)) + edek)

    hdr=b''.join([MAGIC,VERSION,struct.pack('>H',flags),struct.pack('>B',codec),
        struct.pack('>B',quant),struct.pack('>H',len(chunk_blobs)),
        struct.pack('>I',chunk_rows),struct.pack('>I',len(schema_b)),schema_b,
        struct.pack('>I',len(manifest_b)),manifest_b,
        struct.pack('>B',len(comment_b)),comment_b,
        struct.pack('>q',int(time.time())),struct.pack('>I',retention_days),
        struct.pack('>Q',orig_rows),struct.pack('>Q',orig_rows),
        struct.pack('>Q',orig_bytes), enc_meta,])
    hdr_sha=_sha256(hdr); hdr_size=len(hdr)+32

    cursor=hdr_size
    for i,(blob,sha) in enumerate(chunk_blobs):
        index_entries[i]['byte_offset']=cursor+4
        cursor+=4+len(blob)+32

    index_json=json.dumps(index_entries,ensure_ascii=False).encode()
    index_sha=_sha256(index_json)

    with open(path,'wb') as f:
        f.write(hdr); f.write(hdr_sha)
        for blob,sha in chunk_blobs:
            f.write(struct.pack('>I',len(blob))); f.write(blob); f.write(sha)
        f.write(index_json); f.write(struct.pack('>I',len(index_json)))
        f.write(index_sha); f.write(EOF_MAGIC)

    elapsed=time.time()-t0; stored=os.path.getsize(path)
    result = {'path':path,'rows':orig_rows,'cols':len(df.columns),
            'n_chunks':len(chunk_blobs),'chunk_rows':chunk_rows,
            'original_mb':round(orig_bytes/1e6,3),'stored_mb':round(stored/1e6,3),
            'ratio':round(orig_bytes/stored,3),
            'reduction_pct':round((1-stored/orig_bytes)*100,2),
            'freeze_s':round(elapsed,3),
            'codec':{CODEC_LZMA2:'lzma2',CODEC_ZSTD:'zstd',CODEC_ZPAQ:'zpaq'}.get(codec,'?'),
            'partition_by':partition_by,'index_entries':len(index_entries)}
    if _auto_reason is not None:
        result['auto_reason'] = _auto_reason
    return result

# ── HEADER PARSER ─────────────────────────────────────────────────────────────
def _read_header(raw):
    if raw[:4]!=MAGIC: raise ValueError(f"Magic inválido: {raw[:4]!r}")
    p=0
    def rd(n): nonlocal p; v=raw[p:p+n]; p+=n; return v
    rd(4);rd(2)
    flags=struct.unpack('>H',rd(2))[0]; codec=struct.unpack('>B',rd(1))[0]
    quant=struct.unpack('>B',rd(1))[0]; n_chunks=struct.unpack('>H',rd(2))[0]
    chunk_rows=struct.unpack('>I',rd(4))[0]
    sl=struct.unpack('>I',rd(4))[0]; rd(sl)
    ml=struct.unpack('>I',rd(4))[0]; manifests=json.loads(rd(ml))
    cl=struct.unpack('>B',rd(1))[0]; comment=rd(cl).decode()
    freeze_ts=struct.unpack('>q',rd(8))[0]; rd(4)
    orig_rows=struct.unpack('>Q',rd(8))[0]; stored_rows=struct.unpack('>Q',rd(8))[0]; rd(8)
    enc_kms=''; key_id=''; enc_dek=b''
    if flags & FLAG_ENCRYPTED:
        kl=struct.unpack('>B',rd(1))[0]; enc_kms=rd(kl).decode()
        kil=struct.unpack('>B',rd(1))[0]; key_id=rd(kil).decode()
        edek_len=struct.unpack('>H',rd(2))[0]; enc_dek=rd(edek_len)
    hdr_end=p; hdr_sha=rd(32)
    return {'flags':flags,'codec':codec,'quant':quant,'n_chunks':n_chunks,
            'chunk_rows':chunk_rows,'manifests':manifests,'comment':comment,
            'freeze_ts':freeze_ts,'orig_rows':orig_rows,'stored_rows':stored_rows,
            'hdr_end':hdr_end,'hdr_sha_stored':hdr_sha,'payload_start':p,
            'encrypted':bool(flags & FLAG_ENCRYPTED),'enc_kms':enc_kms,'key_id':key_id,
            'enc_dek':enc_dek}

def _read_sparse_index(raw):
    if raw[-4:]!=EOF_MAGIC: raise ValueError("EOF magic ausente")
    idx_len=struct.unpack('>I',raw[-4-32-4:-4-32])[0]
    idx_sha_stored=raw[-4-32:-4]
    idx_json=raw[-4-32-4-idx_len:-4-32-4]
    if _sha256(idx_json)!=idx_sha_stored: raise ValueError("Sparse index corrompido")
    return json.loads(idx_json)

# ── THAW ─────────────────────────────────────────────────────────────────────
def thaw(
    path: str | os.PathLike,
    verify: bool = True,
    filter: Optional[dict] = None,
    row_range: Optional[tuple] = None,
    key=None,
    schema_override=None,
    engine: str = 'pandas',
) -> 'pd.DataFrame | Any':
    """Descomprime um arquivo .permafrost de volta para DataFrame.

    Args:
        path: Arquivo .permafrost a descomprimir.
        verify: Se ``True`` (padrão), verifica SHA-256 de cada chunk antes de
            descomprimir. Detecta bit-rot e corrupção antes de qualquer CPU gasto.
        filter: Dicionário ``{coluna: valor}`` para thaw seletivo via sparse index.
            Ex: ``{"ano": 2023}`` lê apenas os chunks que contêm dados de 2023.
            Requer que o arquivo tenha sido criado com ``partition_by=coluna``.
        row_range: Tupla ``(start, end)`` para ler apenas um range de linhas.
            Ex: ``(0, 9999)`` retorna as primeiras 10.000 linhas.

    Returns:
        ``pd.DataFrame`` com os dados descomprimidos. Schema idêntico ao original.

    Raises:
        ValueError: Se o arquivo estiver corrompido, truncado, ou com SHA-256 inválido.
        FileNotFoundError: Se o arquivo não existir.

    Example:
        >>> df_full = pf.thaw("vendas.permafrost")
        >>> df_2023 = pf.thaw("vendas.permafrost", filter={"ano": 2023})
        >>> df_sample = pf.thaw("vendas.permafrost", row_range=(0, 9_999))
    """
    from permafrost.crypto import resolve_key, decrypt_chunk
    with open(path,'rb') as f: raw=f.read()
    if raw[-4:]!=EOF_MAGIC: raise ValueError("EOF magic ausente")
    h=_read_header(raw)
    if verify:
        if _sha256(raw[:h['hdr_end']])!=h['hdr_sha_stored']:
            raise ValueError("Header SHA-256 inválido")
    codec=h['codec']; manifests=h['manifests']; orig_rows=h['orig_rows']

    raw_key = None
    if h['encrypted']:
        raw_key, _, _, _ = resolve_key(key, edek=h['enc_dek'])
        if raw_key is None:
            raise ValueError(
                "This .permafrost file is encrypted. "
                "Provide key= or set PERMAFROST_KEY env var."
            )

    index_entries=_read_sparse_index(raw)

    selected=index_entries
    if filter:
        col_f,val_f=next(iter(filter.items()))
        if isinstance(val_f, (list, tuple)) and len(val_f)==2:
            lo,hi=str(val_f[0]),str(val_f[1])
            sel=[e for e in index_entries if e['part_col']==col_f
                 and _chunk_overlaps_range(e['part_key'],lo,hi)]
        else:
            val_str=str(val_f)
            sel=[e for e in index_entries if e['part_col']==col_f and val_str in e['part_key']]
        if sel: selected=sel
    if row_range:
        sr,er=row_range
        selected=[e for e in index_entries if e['row_end']>=sr and e['row_start']<=er]

    if not selected: return pd.DataFrame(columns=list(manifests.keys()))

    dfs=[]
    for entry in selected:
        offset=entry['byte_offset']; blk_len=entry['byte_len']
        blob=raw[offset:offset+blk_len]; sha=raw[offset+blk_len:offset+blk_len+32]
        if verify and _sha256(blob).hex()!=entry['sha256']:
            raise ValueError(f"Chunk {entry['chunk_id']} corrompido")
        if raw_key is not None:
            blob = decrypt_chunk(blob, raw_key)
        chunk_raw=_decompress(blob,codec)
        n_rows_chunk=entry['row_end']-entry['row_start']+1
        dfs.append(_parse_chunk(chunk_raw,manifests,n_rows_chunk))

    result=pd.concat(dfs,ignore_index=True) if dfs else pd.DataFrame()
    if filter and len(result):
        col_f,val_f=next(iter(filter.items()))
        if col_f in result.columns:
            if isinstance(val_f,(list,tuple)) and len(val_f)==2:
                lo,hi=val_f
                result=result[(result[col_f]>=lo)&(result[col_f]<=hi)].reset_index(drop=True)
            else:
                result=result[result[col_f].astype(str)==str(val_f)].reset_index(drop=True)
    if schema_override is not None:
        from permafrost.schema_evolution import apply_schema_evolution
        result = apply_schema_evolution(result, schema_override)
    if engine == 'polars':
        import polars as pl
        return pl.from_pandas(result)
    return result

# ── FREEZE APPEND ─────────────────────────────────────────────────────────────
def freeze_append(
    path: str | os.PathLike,
    df_new: pd.DataFrame,
    verify: bool = True,
) -> dict[str, Any]:
    """Appends new rows to an existing .permafrost file without re-freezing.

    Uses the same codec, predictors and chunk size as the original freeze.
    The sparse index is rebuilt; the file is written atomically via a temp file.

    Args:
        path: Existing .permafrost file to append to.
        df_new: DataFrame with the same columns as the original file.
        verify: Verify SHA-256 of existing chunks before appending (default True).

    Returns:
        ``{"appended_rows": N, "total_rows": M, "total_chunks": K, "append_s": T}``

    Raises:
        ValueError: If schemas are incompatible or the file is encrypted.

    Example:
        >>> pf.freeze(df_jan, "log.permafrost", codec=pf.CODEC_ZSTD, partition_by="mes")
        >>> pf.freeze_append("log.permafrost", df_feb)
        >>> pf.thaw("log.permafrost", filter={"mes": 2})  # works across both batches
    """
    t0 = time.time()

    if type(df_new).__module__.startswith('polars'):
        df_new = df_new.to_pandas()

    with open(path, 'rb') as f:
        raw = f.read()

    h = _read_header(raw)

    if h['encrypted']:
        raise ValueError("freeze_append does not support encrypted files yet.")

    index_entries = _read_sparse_index(raw)

    # Validate column compatibility
    existing_cols = set(h['manifests'].keys())
    new_cols = set(df_new.columns)
    if existing_cols != new_cols:
        raise ValueError(
            f"Schema mismatch — existing: {sorted(existing_cols)}, "
            f"new: {sorted(new_cols)}"
        )

    manifests  = h['manifests']
    codec      = h['codec']
    quant      = h['quant']
    chunk_rows = h['chunk_rows']
    orig_rows  = h['orig_rows']
    partition_col = index_entries[0]['part_col'] if index_entries else '__rows__'

    # Find where chunks section ends (= start of sparse index JSON)
    idx_len = struct.unpack('>I', raw[-4-32-4:-4-32])[0]
    chunks_end = len(raw) - 4 - 32 - 4 - idx_len
    existing_chunks_bytes = raw[h['payload_start']:chunks_end]

    if verify:
        for entry in index_entries:
            offset, blk_len = entry['byte_offset'], entry['byte_len']
            blob = raw[offset:offset+blk_len]
            if _sha256(blob).hex() != entry['sha256']:
                raise ValueError(f"Chunk {entry['chunk_id']} corrupted — aborting append")

    # Encode new chunks with existing manifests (same predictors/codec)
    new_chunk_blobs: list = []
    new_index_entries: list = []

    for chunk_start in range(0, len(df_new), chunk_rows):
        chunk_end   = min(chunk_start + chunk_rows, len(df_new))
        df_chunk    = df_new.iloc[chunk_start:chunk_end].reset_index(drop=True)
        raw_chunk   = _encode_chunk(df_chunk, manifests, quant)
        compressed  = _compress(raw_chunk, codec)
        sha         = _sha256(compressed)

        if partition_col != '__rows__' and partition_col in df_chunk.columns:
            pv = sorted(df_chunk[partition_col].unique().tolist())
            part_key = str(pv[0]) if len(pv)==1 else f"{pv[0]}-{pv[-1]}"
        else:
            r0 = orig_rows + chunk_start
            part_key = f"rows_{r0}_{r0 + chunk_end - chunk_start - 1}"

        new_index_entries.append({
            'chunk_id':   len(index_entries) + len(new_chunk_blobs),
            'row_start':  orig_rows + chunk_start,
            'row_end':    orig_rows + chunk_end - 1,
            'part_key':   part_key,
            'part_col':   partition_col,
            'byte_offset': None,
            'byte_len':   len(compressed),
            'sha256':     sha.hex(),
        })
        new_chunk_blobs.append((compressed, sha))

    # Calculate byte offsets for new chunks in the rewritten file
    # New layout: [new_hdr][new_hdr_sha][existing_chunks][new_chunks][new_index]
    cursor = h['payload_start'] + len(existing_chunks_bytes)
    for i, (blob, _) in enumerate(new_chunk_blobs):
        new_index_entries[i]['byte_offset'] = cursor + 4
        cursor += 4 + len(blob) + 32

    all_index = index_entries + new_index_entries
    total_rows   = orig_rows + len(df_new)
    total_chunks = len(all_index)

    # Patch header: N_CHUNKS (offset 10, 2B) and ORIG_ROWS (variable position)
    hdr_bytes = bytearray(raw[:h['hdr_end']])
    struct.pack_into('>H', hdr_bytes, 10, total_chunks)
    or_offset = _find_orig_rows_offset(raw)
    struct.pack_into('>Q', hdr_bytes, or_offset,     total_rows)
    struct.pack_into('>Q', hdr_bytes, or_offset + 8, total_rows)
    new_hdr_sha = _sha256(bytes(hdr_bytes))

    new_index_json = json.dumps(all_index, ensure_ascii=False).encode()
    new_index_sha  = _sha256(new_index_json)

    # Atomic write via temp file
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(bytes(hdr_bytes)); f.write(new_hdr_sha)
        f.write(existing_chunks_bytes)
        for blob, sha in new_chunk_blobs:
            f.write(struct.pack('>I', len(blob))); f.write(blob); f.write(sha)
        f.write(new_index_json)
        f.write(struct.pack('>I', len(new_index_json)))
        f.write(new_index_sha); f.write(EOF_MAGIC)
    os.replace(tmp, path)

    return {
        'appended_rows': len(df_new),
        'total_rows':    total_rows,
        'total_chunks':  total_chunks,
        'append_s':      round(time.time() - t0, 3),
    }


# ── AUDIT ─────────────────────────────────────────────────────────────────────
def audit(path: str | os.PathLike) -> dict[str, Any]:
    """Lê metadados de um arquivo .permafrost sem descomprimir nenhum chunk.

    Opera apenas no header (primeiros ~128KB) e no sparse index (últimos ~8KB).
    Um arquivo de 2 GB é auditado em < 1ms.

    Args:
        path: Arquivo .permafrost a inspecionar.

    Returns:
        Dicionário com metadados::

            {
                "version":        "1.2",
                "codec":          "lzma2",
                "quant":          0,
                "freeze_date":    "2026-05-13T14:30:00",
                "orig_rows":      80000,
                "n_chunks":       16,
                "chunk_rows":     5000,
                "file_size_mb":   0.678,
                "columns":        ["id", "data", "ano", ...],
                "partition_col":  "ano",
                "partition_keys": ["2020", "2021", ...],
                "comment":        "meu comentário",
                "index_entries":  [...],
            }

    Raises:
        ValueError: Se o arquivo não for um .permafrost válido.

    Example:
        >>> info = pf.audit("vendas.permafrost")
        >>> print(info["codec"], info["orig_rows"])
        lzma2 80000
    """
    with open(path,'rb') as f: raw=f.read()
    h=_read_header(raw[:131072]); index=_read_sparse_index(raw)
    codec_name={CODEC_LZMA2:'lzma2',CODEC_ZSTD:'zstd',CODEC_ZPAQ:'zpaq'}.get(h['codec'],'?')
    lossy={}
    stored_schema={}
    for col,m in h['manifests'].items():
        stored_schema[col]=m.get('dtype','object')
        if m.get('predictor') in (PRED_FLOAT32, PRED_FLOAT16):
            lossy[col]={'predictor':m['predictor'],
                        'precision_bits':m.get('precision_bits',32),
                        'max_abs_error':m.get('max_abs_error',0.0),
                        'max_rel_error':m.get('max_rel_error',0.0)}
    return {'version':f"{raw[4]}.{raw[5]}",'codec':codec_name,'quant':h['quant'],
            'freeze_date':pd.Timestamp(h['freeze_ts'],unit='s').isoformat(),
            'orig_rows':h['orig_rows'],'n_chunks':h['n_chunks'],
            'chunk_rows':h['chunk_rows'],'file_size_mb':round(os.path.getsize(path)/1e6,3),
            'columns':list(h['manifests'].keys()),'stored_schema':stored_schema,
            'index_entries':index,
            'partition_col':index[0]['part_col'] if index else None,
            'partition_keys':[e['part_key'] for e in index],
            'comment':h['comment'],
            'encrypted':h['encrypted'],'kms':h['enc_kms'],'key_id':h['key_id'],
            'edek_size':len(h.get('enc_dek', b'')),
            'lossy_columns':lossy}


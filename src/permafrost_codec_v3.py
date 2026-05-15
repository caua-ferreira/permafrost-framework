"""
PermafrostCodec v3 — formato .permafrost com Sparse Index
Adições sobre v2:
  - Payload dividido em chunks independentemente compressíveis
  - Sparse Index ao final do arquivo (inspirado no Parquet footer)
  - thaw() suporta filter= para leitura seletiva sem descomprimir tudo
  - Footer layout: [INDEX_JSON][INDEX_LEN:u32][INDEX_SHA256:32B][EOF_MAGIC:4B]
"""
import struct, hashlib, io, json, time, os, lzma
import numpy as np, pandas as pd
import zstandard as zstd

# ── CONSTANTES ───────────────────────────────────────────────────────────────
MAGIC       = b'PRMS'
EOF_MAGIC   = b'SMRP'
VERSION     = bytes([1, 1])   # v1.1 — adicionado Sparse Index

CODEC_ZSTD  = 0x01
CODEC_LZMA2 = 0x02
CODEC_ZPAQ  = 0x03

QUANT_NONE   = 0x00
QUANT_HIGH   = 0x01
QUANT_MEDIUM = 0x02
QUANT_LOW    = 0x03

FLAG_DELTA     = 0x01
FLAG_QUANTIZE  = 0x02
FLAG_CHUNKED   = 0x04   # novo — payload em chunks
FLAG_PREDICTOR = 0x08
FLAG_INDEX     = 0x10   # novo — sparse index presente

DEFAULT_CHUNK_ROWS = 10_000   # linhas por chunk

def _sha256(b): return hashlib.sha256(b).digest()

# ── PREDICTORES (idênticos à v2) ─────────────────────────────────────────────
PRED_DELTA    = 'delta_zigzag'
PRED_LAG1     = 'lag1_zigzag'
PRED_CATEGORY = 'category_u8'
PRED_TS       = 'ts_delta_s'
PRED_RAW      = 'raw_text'

def _zigzag_enc(a): return np.where(a>=0, a*2, -a*2-1).astype(np.uint64)
def _zigzag_dec(a): return np.where(a%2==0, a//2, -(a.astype(np.int64)//2)-1).astype(np.int64)

def encode_column(name, series, quant):
    m = {'name': name, 'dtype': str(series.dtype), 'predictor': None, 'scale': 1}
    if pd.api.types.is_datetime64_any_dtype(series):
        ts = series.astype('int64') // 10**9
        if quant >= QUANT_MEDIUM:
            ts = (ts // 60) * 60; m['quant'] = 'floor_minute'
        deltas = np.diff(ts.values.astype(np.int64), prepend=0)
        m['predictor'] = PRED_TS
        return _zigzag_enc(deltas).astype(np.uint32).tobytes(), m
    is_int = pd.api.types.is_integer_dtype(series)
    is_id  = name == 'id' or name.lower().endswith('_id')
    if is_int and is_id:
        vals = series.values.astype(np.int64)
        deltas = np.diff(vals, prepend=0)
        m['predictor'] = PRED_DELTA
        return _zigzag_enc(deltas).astype(np.uint32).tobytes(), m
    if pd.api.types.is_float_dtype(series):
        scale = 100
        if quant >= QUANT_MEDIUM:
            if 'lat' in name or 'lon' in name: series=series.round(4); scale=10_000; m['quant']='round4'
            elif any(x in name for x in ['score','pct']): series=series.round(1); scale=10; m['quant']='round1'
            else: series=series.round(0); scale=1; m['quant']='round0'
        else:
            if 'lat' in name or 'lon' in name: scale=1_000_000
        vals = np.round(series.values * scale).astype(np.int64)
        pred = np.empty_like(vals); pred[0]=0; pred[1:]=vals[:-1]
        m['predictor'] = PRED_LAG1; m['scale'] = scale
        return _zigzag_enc(vals-pred).astype(np.uint32).tobytes(), m
    if is_int:
        vals = series.values.astype(np.int64)
        deltas = np.diff(vals, prepend=0)
        m['predictor'] = PRED_DELTA
        return _zigzag_enc(deltas).astype(np.uint32).tobytes(), m
    if series.nunique() <= 256:
        cats = series.astype('category')
        m['categories'] = list(cats.cat.categories.astype(str))
        m['predictor']  = PRED_CATEGORY
        return cats.cat.codes.values.astype(np.uint8).tobytes(), m
    m['predictor'] = PRED_RAW
    return '\x00'.join(series.astype(str).values).encode('utf-8'), m

def decode_column(data, manifest, n_rows):
    pred  = manifest.get('predictor')
    scale = manifest.get('scale', 1)
    name  = manifest['name']
    if pred == PRED_TS:
        arr = np.frombuffer(data, dtype=np.uint32).astype(np.uint64)
        return pd.Series(pd.to_datetime(np.cumsum(_zigzag_dec(arr)), unit='s'), name=name)
    if pred == PRED_DELTA:
        arr = np.frombuffer(data, dtype=np.uint32).astype(np.uint64)
        return pd.Series(np.cumsum(_zigzag_dec(arr)), name=name)
    if pred == PRED_LAG1:
        arr = np.frombuffer(data, dtype=np.uint32).astype(np.uint64)
        return pd.Series(np.cumsum(_zigzag_dec(arr)) / scale, name=name)
    if pred == PRED_CATEGORY:
        codes = np.frombuffer(data, dtype=np.uint8)
        cats  = manifest.get('categories', [])
        return pd.Series(pd.Categorical.from_codes(codes, categories=cats), name=name)
    if pred == PRED_RAW:
        return pd.Series(data.decode('utf-8').split('\x00'), name=name)
    return pd.Series([None]*n_rows, name=name)

# ── SERIALIZAÇÃO DE UM CHUNK ─────────────────────────────────────────────────
def _encode_chunk(df_chunk: pd.DataFrame, manifests: dict, quant: int) -> bytes:
    """Serializa as colunas de um chunk: [n_cols:u16][name_len:u8][name][len:u32][data]..."""
    # Na primeira chamada, criar manifests; nas demais, reusar
    payload = struct.pack('>H', len(df_chunk.columns))
    for col in df_chunk.columns:
        if col not in manifests:
            _, m = encode_column(col, df_chunk[col], quant)
            manifests[col] = m
        enc, _ = encode_column(col, df_chunk[col], quant)
        nb = col.encode()
        payload += struct.pack('>B', len(nb)) + nb
        payload += struct.pack('>I', len(enc)) + enc
    return payload

def _compress_bytes(data: bytes, codec: int) -> bytes:
    if codec == CODEC_LZMA2:
        return lzma.compress(data, format=lzma.FORMAT_XZ, preset=lzma.PRESET_EXTREME|9)
    return zstd.ZstdCompressor(level=19, threads=2).compress(data)

def _decompress_bytes(data: bytes, codec: int) -> bytes:
    if codec == CODEC_LZMA2:
        return lzma.decompress(data, format=lzma.FORMAT_XZ)
    return zstd.ZstdDecompressor().decompress(data)

def _parse_chunk(data: bytes, manifests: dict, n_rows: int) -> pd.DataFrame:
    """Reconstrói DataFrame a partir de bytes de um chunk deserializado."""
    p = 0
    n_cols = struct.unpack_from('>H', data, p)[0]; p += 2
    col_data = {}
    for _ in range(n_cols):
        nl = struct.unpack_from('>B', data, p)[0]; p += 1
        cn = data[p:p+nl].decode(); p += nl
        sl = struct.unpack_from('>I', data, p)[0]; p += 4
        col_data[cn] = data[p:p+sl]; p += sl
    series = {}
    for col, m in manifests.items():
        if col in col_data:
            series[col] = decode_column(col_data[col], m, n_rows)
    return pd.DataFrame(series)

def _pa_schema(df):
    try:
        import pyarrow as pa
        return pa.Schema.from_pandas(df).serialize().to_pybytes()
    except: return b'{}'

# ── FREEZE ────────────────────────────────────────────────────────────────────
def freeze(df: pd.DataFrame, path: str,
           codec: int = CODEC_LZMA2,
           quant: int  = QUANT_NONE,
           chunk_rows: int = DEFAULT_CHUNK_ROWS,
           partition_by: str = None,
           comment: str = "",
           retention_days: int = 0) -> dict:
    """
    Comprime DataFrame para .permafrost com Sparse Index.

    partition_by: coluna para usar como chave do índice (ex: 'ano', 'regiao').
                  Se None, usa row ranges.
    """
    t0 = time.time()
    orig_bytes = len(df.to_csv(index=False).encode())
    orig_rows  = len(df)
    flags = FLAG_PREDICTOR | FLAG_DELTA | FLAG_CHUNKED | FLAG_INDEX
    if quant: flags |= FLAG_QUANTIZE

    # ── 1. Dividir em chunks e comprimir cada um ──────────────────────────────
    manifests    = {}   # preenchido na primeira iteração
    chunk_blobs  = []   # bytes comprimidos por chunk
    index_entries = []  # sparse index

    for chunk_start in range(0, orig_rows, chunk_rows):
        chunk_end = min(chunk_start + chunk_rows, orig_rows)
        df_chunk  = df.iloc[chunk_start:chunk_end].reset_index(drop=True)

        raw_chunk = _encode_chunk(df_chunk, manifests, quant)
        compressed = _compress_bytes(raw_chunk, codec)
        sha = _sha256(compressed)

        # Chave de partição
        if partition_by and partition_by in df_chunk.columns:
            part_vals = sorted(df_chunk[partition_by].unique().tolist())
            part_key  = str(part_vals[0]) if len(part_vals)==1 else f"{part_vals[0]}-{part_vals[-1]}"
        else:
            part_key = f"rows_{chunk_start}_{chunk_end-1}"

        index_entries.append({
            'chunk_id':   len(chunk_blobs),
            'row_start':  chunk_start,
            'row_end':    chunk_end - 1,
            'part_key':   part_key,
            'part_col':   partition_by or '__rows__',
            'byte_offset': None,   # preenchido depois
            'byte_len':   len(compressed),
            'sha256':     sha.hex(),
        })
        chunk_blobs.append((compressed, sha))

    # ── 2. Calcular offsets reais (após header) ───────────────────────────────
    schema_b   = _pa_schema(df)
    manifest_b = json.dumps(manifests, ensure_ascii=False, default=str).encode()
    comment_b  = comment.encode()[:255]

    hdr_body = b''.join([
        MAGIC, VERSION,
        struct.pack('>H', flags),
        struct.pack('>B', codec),
        struct.pack('>B', quant),
        struct.pack('>H', len(chunk_blobs)),        # n_chunks
        struct.pack('>I', chunk_rows),              # rows_per_chunk
        struct.pack('>I', len(schema_b)), schema_b,
        struct.pack('>I', len(manifest_b)), manifest_b,
        struct.pack('>B', len(comment_b)), comment_b,
        struct.pack('>q', int(time.time())),
        struct.pack('>I', retention_days),
        struct.pack('>Q', orig_rows),
        struct.pack('>Q', orig_rows),
        struct.pack('>Q', orig_bytes),
    ])
    hdr_sha  = _sha256(hdr_body)
    hdr_size = len(hdr_body) + 32   # + sha256

    # Offset de cada chunk no arquivo
    cursor = hdr_size
    for i, (blob, sha) in enumerate(chunk_blobs):
        # Cada chunk: [chunk_len:u32][data][sha256:32B]
        index_entries[i]['byte_offset'] = cursor + 4   # após o len field
        cursor += 4 + len(blob) + 32

    # ── 3. Sparse Index ───────────────────────────────────────────────────────
    index_json = json.dumps(index_entries, ensure_ascii=False).encode()
    index_sha  = _sha256(index_json)

    # ── 4. Escrever arquivo ───────────────────────────────────────────────────
    with open(path, 'wb') as f:
        f.write(hdr_body)
        f.write(hdr_sha)
        for blob, sha in chunk_blobs:
            f.write(struct.pack('>I', len(blob)))
            f.write(blob)
            f.write(sha)
        # Footer: index_json + index_len(u32) + index_sha(32B) + EOF_MAGIC(4B)
        f.write(index_json)
        f.write(struct.pack('>I', len(index_json)))
        f.write(index_sha)
        f.write(EOF_MAGIC)

    elapsed = time.time() - t0
    stored  = os.path.getsize(path)
    return {
        'path': path, 'rows': orig_rows, 'cols': len(df.columns),
        'n_chunks': len(chunk_blobs), 'chunk_rows': chunk_rows,
        'original_mb': round(orig_bytes/1e6,3), 'stored_mb': round(stored/1e6,3),
        'ratio': round(orig_bytes/stored, 3),
        'reduction_pct': round((1-stored/orig_bytes)*100, 2),
        'freeze_s': round(elapsed,3),
        'codec': {CODEC_LZMA2:'lzma2',CODEC_ZSTD:'zstd'}.get(codec,'?'),
        'partition_by': partition_by,
        'index_entries': len(index_entries),
    }

# ── _read_header ──────────────────────────────────────────────────────────────
def _read_header(raw: bytes) -> dict:
    """Parseia header e retorna dict com todos os campos + posição após header."""
    if raw[:4] != MAGIC: raise ValueError(f"Magic inválido: {raw[:4]!r}")
    p = 0
    def rd(n): nonlocal p; v=raw[p:p+n]; p+=n; return v
    rd(4); rd(2)                          # magic, version
    flags   = struct.unpack('>H',rd(2))[0]
    codec   = struct.unpack('>B',rd(1))[0]
    quant   = struct.unpack('>B',rd(1))[0]
    n_chunks = struct.unpack('>H',rd(2))[0]
    chunk_rows = struct.unpack('>I',rd(4))[0]
    sl = struct.unpack('>I',rd(4))[0]; rd(sl)
    ml = struct.unpack('>I',rd(4))[0]; manifests=json.loads(rd(ml))
    cl = struct.unpack('>B',rd(1))[0]; comment=rd(cl).decode()
    freeze_ts  = struct.unpack('>q',rd(8))[0]
    rd(4)      # retention
    orig_rows  = struct.unpack('>Q',rd(8))[0]
    stored_rows= struct.unpack('>Q',rd(8))[0]
    rd(8)      # orig_bytes
    hdr_end = p
    hdr_sha_stored = rd(32)
    return {
        'flags':flags,'codec':codec,'quant':quant,'n_chunks':n_chunks,
        'chunk_rows':chunk_rows,'manifests':manifests,'comment':comment,
        'freeze_ts':freeze_ts,'orig_rows':orig_rows,'stored_rows':stored_rows,
        'hdr_end':hdr_end,'hdr_sha_stored':hdr_sha_stored,
        'payload_start': p,
    }

# ── _read_sparse_index ────────────────────────────────────────────────────────
def _read_sparse_index(raw: bytes) -> list:
    """Lê o sparse index do footer sem descomprimir chunks."""
    if raw[-4:] != EOF_MAGIC:
        raise ValueError("EOF magic ausente — arquivo corrompido ou truncado")
    # Footer (de trás pra frente): [EOF_MAGIC:4B][INDEX_SHA:32B][INDEX_LEN:4B][INDEX_JSON:var]
    idx_len = struct.unpack('>I', raw[-4-32-4:-4-32])[0]
    idx_sha_stored = raw[-4-32:-4]
    idx_json = raw[-4-32-4-idx_len:-4-32-4]
    idx_sha_computed = _sha256(idx_json)
    if idx_sha_computed != idx_sha_stored:
        raise ValueError("Sparse index corrompido — SHA-256 não confere")
    return json.loads(idx_json)

# ── THAW ──────────────────────────────────────────────────────────────────────
def thaw(path: str, verify: bool = True,
         filter: dict = None,
         row_range: tuple = None) -> pd.DataFrame:
    """
    Descomprime .permafrost com suporte a leitura seletiva.

    filter: dict de {coluna: valor} para selecionar chunks por partition_key.
            Ex: {'ano': 2021} ou {'regiao': 'Sul'}
    row_range: tuple (start, end) para ler apenas linhas específicas.
            Ex: (10000, 30000)
    """
    with open(path, 'rb') as f:
        raw = f.read()

    # 1. Verificar EOF
    if raw[-4:] != EOF_MAGIC:
        raise ValueError("EOF magic ausente — arquivo corrompido ou truncado")

    # 2. Parsear header
    h = _read_header(raw)
    if verify:
        computed = _sha256(raw[:h['hdr_end']])
        if computed != h['hdr_sha_stored']:
            raise ValueError("Header SHA-256 inválido — arquivo modificado")

    codec    = h['codec']
    manifests= h['manifests']
    orig_rows= h['orig_rows']

    # 3. Ler sparse index do footer
    index_entries = _read_sparse_index(raw)

    # 4. Selecionar chunks relevantes
    selected = index_entries   # default: todos

    if filter:
        # Filtrar por partition key
        col_filter, val_filter = next(iter(filter.items()))
        val_str = str(val_filter)
        selected = [e for e in index_entries
                    if e['part_col'] == col_filter and val_str in e['part_key']]
        if not selected:
            # Fallback: row-based
            selected = [e for e in index_entries if e['part_col'] == '__rows__']

    if row_range:
        start_r, end_r = row_range
        selected = [e for e in index_entries
                    if e['row_end'] >= start_r and e['row_start'] <= end_r]

    if not selected:
        return pd.DataFrame(columns=list(manifests.keys()))

    # 5. Ler e descomprimir apenas os chunks selecionados
    dfs = []
    bytes_read = 0
    for entry in selected:
        offset  = entry['byte_offset']
        blk_len = entry['byte_len']
        sha_hex = entry['sha256']

        # byte_offset aponta para o início dos dados (após o u32 de len)
        blob = raw[offset: offset + blk_len]
        sha  = raw[offset + blk_len: offset + blk_len + 32]
        bytes_read += blk_len + 32

        if verify and _sha256(blob).hex() != sha_hex:
            raise ValueError(f"Chunk {entry['chunk_id']} corrompido — SHA-256 não confere")

        chunk_raw = _decompress_bytes(blob, codec)
        n_rows_chunk = entry['row_end'] - entry['row_start'] + 1
        df_chunk = _parse_chunk(chunk_raw, manifests, n_rows_chunk)
        dfs.append(df_chunk)

    result = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    # 6. Aplicar filtro preciso se necessário
    if filter and result is not None and len(result):
        col_f, val_f = next(iter(filter.items()))
        if col_f in result.columns:
            result = result[result[col_f].astype(str) == str(val_f)].reset_index(drop=True)

    if row_range and result is not None and len(result):
        start_r, end_r = row_range
        result = result.iloc[:end_r - start_r + 1].reset_index(drop=True)

    return result

# ── AUDIT ─────────────────────────────────────────────────────────────────────
def audit(path: str) -> dict:
    """Lê header + sparse index sem descomprimir nenhum chunk."""
    with open(path,'rb') as f: raw=f.read()
    h = _read_header(raw[:131072])
    index_entries = _read_sparse_index(raw)
    codec_name = {CODEC_LZMA2:'lzma2', CODEC_ZSTD:'zstd'}.get(h['codec'],'?')
    total_payload = sum(e['byte_len'] for e in index_entries)
    orig_bytes = h['orig_rows']   # aproximação
    return {
        'version':      f"{raw[4]}.{raw[5]}",
        'codec':        codec_name,
        'quant':        h['quant'],
        'freeze_date':  pd.Timestamp(h['freeze_ts'], unit='s').isoformat(),
        'orig_rows':    h['orig_rows'],
        'n_chunks':     h['n_chunks'],
        'chunk_rows':   h['chunk_rows'],
        'file_size_mb': round(os.path.getsize(path)/1e6, 3),
        'columns':      list(h['manifests'].keys()),
        'index_entries': index_entries,
        'partition_col': index_entries[0]['part_col'] if index_entries else None,
        'partition_keys': [e['part_key'] for e in index_entries],
    }

print("permafrost_v3.py carregado — v1.1 com Sparse Index")
print(f"  Novos flags: FLAG_CHUNKED=0x04, FLAG_INDEX=0x10")
print(f"  Novo param freeze(): partition_by, chunk_rows")
print(f"  Novo param thaw(): filter={{}}, row_range=()")

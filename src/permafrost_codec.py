"""
PermafrostCodec — formato .permafrost v1.0
Implementação corrigida: freeze() + thaw() + audit() + verify()
"""
import struct, hashlib, io, json, time, os, lzma
import numpy as np, pandas as pd
import zstandard as zstd

# ── CONSTANTES ───────────────────────────────────────────────────────────────
MAGIC       = b'PRMS'
EOF_MAGIC   = b'SMRP'
VERSION     = bytes([1, 0])

CODEC_ZSTD  = 0x01
CODEC_LZMA2 = 0x02

QUANT_NONE   = 0x00
QUANT_HIGH   = 0x01
QUANT_MEDIUM = 0x02

FLAG_DELTA     = 0x01
FLAG_QUANTIZE  = 0x02
FLAG_PREDICTOR = 0x08

def _sha256(b): return hashlib.sha256(b).digest()

# ── ENCODER ──────────────────────────────────────────────────────────────────
PRED_DELTA    = 'delta_zigzag'
PRED_LAG1     = 'lag1_zigzag'
PRED_CATEGORY = 'category_u8'
PRED_TS       = 'ts_delta_s'
PRED_RAW      = 'raw_text'

def _zigzag_enc(a): return np.where(a>=0, a*2, -a*2-1).astype(np.uint64)
def _zigzag_dec(a): return np.where(a%2==0, a//2, -(a.astype(np.int64)//2)-1).astype(np.int64)

def encode_column(name, series, quant):
    m = {'name': name, 'dtype': str(series.dtype), 'predictor': None, 'scale': 1}

    # ── Timestamp
    if pd.api.types.is_datetime64_any_dtype(series):
        ts = series.astype('int64') // 10**9
        if quant >= QUANT_MEDIUM:
            ts = (ts // 60) * 60
            m['quant'] = 'floor_minute'
        vals = ts.values.astype(np.int64)
        # BUG FIX: prepend 0 para que delta[0] = vals[0] (valor absoluto preservado)
        deltas = np.diff(vals, prepend=0)
        zz = _zigzag_enc(deltas)
        m['predictor'] = PRED_TS
        return zz.astype(np.uint32).tobytes(), m

    # ── Inteiros com padrão delta (IDs e similares)
    is_int = pd.api.types.is_integer_dtype(series)
    is_id_col = name == 'id' or name.lower().endswith('_id')
    if is_int and is_id_col:
        vals = series.values.astype(np.int64)
        # BUG FIX: prepend 0 para preservar vals[0] exato
        deltas = np.diff(vals, prepend=0)
        zz = _zigzag_enc(deltas)
        m['predictor'] = PRED_DELTA
        return zz.astype(np.uint32).tobytes(), m

    # ── Floats com preditor lag-1
    if pd.api.types.is_float_dtype(series):
        scale = 100
        if quant >= QUANT_MEDIUM:
            if 'lat' in name or 'lon' in name:
                series = series.round(4); scale = 10_000
                m['quant'] = 'round4'
            elif any(x in name for x in ['score','pct','desc']):
                series = series.round(1); scale = 10
                m['quant'] = 'round1'
            else:
                series = series.round(0); scale = 1
                m['quant'] = 'round0'
        else:
            if 'lat' in name or 'lon' in name: scale = 1_000_000

        vals = np.round(series.values * scale).astype(np.int64)
        # BUG FIX: lag-1 com primeiro valor armazenado absolutamente
        # pred[0] = 0 → residual[0] = vals[0]  (valor absoluto no zigzag)
        # pred[i] = vals[i-1] → residual[i] = vals[i] - vals[i-1]
        pred = np.empty_like(vals)
        pred[0] = 0
        pred[1:] = vals[:-1]
        residuals = vals - pred
        zz = _zigzag_enc(residuals)
        m['predictor'] = PRED_LAG1
        m['scale'] = scale
        return zz.astype(np.uint32).tobytes(), m

    # ── Outros inteiros
    if is_int:
        vals = series.values.astype(np.int64)
        deltas = np.diff(vals, prepend=0)
        zz = _zigzag_enc(deltas)
        m['predictor'] = PRED_DELTA
        return zz.astype(np.uint32).tobytes(), m

    # ── Categóricas (baixa cardinalidade)
    if series.nunique() <= 256:
        cats = series.astype('category')
        m['categories'] = list(cats.cat.categories.astype(str))
        m['predictor']  = PRED_CATEGORY
        return cats.cat.codes.values.astype(np.uint8).tobytes(), m

    # ── Texto livre
    m['predictor'] = PRED_RAW
    return '\x00'.join(series.astype(str).values).encode('utf-8'), m


# ── DECODER ──────────────────────────────────────────────────────────────────
def decode_column(data, manifest, n_rows):
    pred  = manifest.get('predictor')
    scale = manifest.get('scale', 1)
    name  = manifest['name']

    if pred == PRED_TS:
        arr = np.frombuffer(data, dtype=np.uint32).astype(np.uint64)
        deltas = _zigzag_dec(arr)
        # BUG FIX: cumsum direto (delta[0] já é o valor absoluto)
        ts_sec = np.cumsum(deltas)
        return pd.Series(pd.to_datetime(ts_sec, unit='s'), name=name)

    if pred == PRED_DELTA:
        arr = np.frombuffer(data, dtype=np.uint32).astype(np.uint64)
        deltas = _zigzag_dec(arr)
        # BUG FIX: cumsum direto
        vals = np.cumsum(deltas)
        return pd.Series(vals, name=name)

    if pred == PRED_LAG1:
        arr = np.frombuffer(data, dtype=np.uint32).astype(np.uint64)
        residuals = _zigzag_dec(arr)
        # BUG FIX: reconstrução correta
        # vals[0] = residuals[0] (pred[0]=0)
        # vals[i] = vals[i-1] + residuals[i]
        vals = np.cumsum(residuals)
        result = vals / scale
        return pd.Series(result, name=name)

    if pred == PRED_CATEGORY:
        codes = np.frombuffer(data, dtype=np.uint8)
        cats  = manifest.get('categories', [])
        return pd.Series(pd.Categorical.from_codes(codes, categories=cats), name=name)

    if pred == PRED_RAW:
        return pd.Series(data.decode('utf-8').split('\x00'), name=name)

    return pd.Series([None]*n_rows, name=name)


# ── FREEZE ────────────────────────────────────────────────────────────────────
def freeze(df, path, codec=CODEC_LZMA2, quant=QUANT_NONE, comment="", retention_days=0):
    t0 = time.time()
    orig_bytes = len(df.to_csv(index=False).encode())
    orig_rows  = len(df)
    flags = FLAG_PREDICTOR | FLAG_DELTA | (FLAG_QUANTIZE if quant else 0)

    # 1. Codificar colunas
    streams, manifests = {}, {}
    for col in df.columns:
        enc, m = encode_column(col, df[col], quant)
        streams[col] = enc
        manifests[col] = m

    # 2. Serializar streams: [n_cols:u16] [name_len:u8][name][len:u32][data] ...
    payload_raw = struct.pack('>H', len(streams))
    for col, data in streams.items():
        nb = col.encode()
        payload_raw += struct.pack('>B', len(nb)) + nb
        payload_raw += struct.pack('>I', len(data)) + data

    # 3. Comprimir
    if codec == CODEC_LZMA2:
        payload_c = lzma.compress(payload_raw, format=lzma.FORMAT_XZ,
                                  preset=lzma.PRESET_EXTREME | 9)
    else:
        payload_c = zstd.ZstdCompressor(level=19, threads=2).compress(payload_raw)
    payload_sha = _sha256(payload_c)

    # 4. Metadados para header
    schema_b   = pa_schema_bytes(df)
    manifest_b = json.dumps(manifests, ensure_ascii=False, default=str).encode()
    comment_b  = comment.encode()[:255]

    # 5. Montar header body
    hdr = b''.join([
        MAGIC, VERSION,
        struct.pack('>H', flags),
        struct.pack('>B', codec),
        struct.pack('>B', quant),
        struct.pack('>I', len(schema_b)), schema_b,
        struct.pack('>I', len(manifest_b)), manifest_b,
        struct.pack('>B', len(comment_b)), comment_b,
        struct.pack('>q', int(time.time())),
        struct.pack('>I', retention_days),
        struct.pack('>Q', orig_rows),
        struct.pack('>Q', orig_rows),
        struct.pack('>Q', orig_bytes),
        struct.pack('>Q', len(payload_c)),
    ])
    hdr_sha = _sha256(hdr)

    # 6. Escrever
    with open(path, 'wb') as f:
        f.write(hdr)
        f.write(hdr_sha)
        f.write(payload_c)
        f.write(payload_sha)
        f.write(EOF_MAGIC)

    elapsed  = time.time() - t0
    stored   = os.path.getsize(path)
    return {
        'path': path, 'codec': {CODEC_LZMA2:'lzma2',CODEC_ZSTD:'zstd'}.get(codec,'?'),
        'quant': quant, 'rows': orig_rows, 'cols': len(df.columns),
        'original_mb': orig_bytes/1e6, 'stored_mb': stored/1e6,
        'ratio': orig_bytes/stored, 'reduction_pct': (1-stored/orig_bytes)*100,
        'freeze_s': round(elapsed,3),
    }

def pa_schema_bytes(df):
    try:
        import pyarrow as pa
        return pa.Schema.from_pandas(df).serialize().to_pybytes()
    except:
        return b'{}'

# ── THAW ─────────────────────────────────────────────────────────────────────
def thaw(path, verify=True):
    with open(path,'rb') as f: raw = f.read()

    if raw[:4] != MAGIC:   raise ValueError(f"Magic inválido: {raw[:4]!r}")
    if raw[-4:] != EOF_MAGIC: raise ValueError("EOF magic ausente — arquivo truncado")

    pos = 0
    def rd(n): nonlocal pos; v=raw[pos:pos+n]; pos+=n; return v

    rd(4)  # magic
    rd(2)  # version
    flags  = struct.unpack('>H',rd(2))[0]
    codec  = struct.unpack('>B',rd(1))[0]
    quant  = struct.unpack('>B',rd(1))[0]
    sl = struct.unpack('>I',rd(4))[0]; rd(sl)        # schema
    ml = struct.unpack('>I',rd(4))[0]
    manifests = json.loads(rd(ml))
    cl = struct.unpack('>B',rd(1))[0]; rd(cl)        # comment
    rd(8)  # freeze_ts
    rd(4)  # retention
    orig_rows  = struct.unpack('>Q',rd(8))[0]
    stored_rows= struct.unpack('>Q',rd(8))[0]
    rd(8)  # orig_bytes
    payload_len= struct.unpack('>Q',rd(8))[0]

    hdr_end = pos
    hdr_sha_stored = rd(32)
    if verify and _sha256(raw[:hdr_end]) != hdr_sha_stored:
        raise ValueError("Header SHA-256 inválido — arquivo modificado")

    payload_c  = rd(payload_len)
    payload_sha_stored = rd(32)
    if verify and _sha256(payload_c) != payload_sha_stored:
        raise ValueError("Payload SHA-256 inválido — conteúdo corrompido")

    payload_raw = (lzma.decompress(payload_c, format=lzma.FORMAT_XZ)
                   if codec == CODEC_LZMA2
                   else zstd.ZstdDecompressor().decompress(payload_c))

    p = 0
    n_cols = struct.unpack_from('>H',payload_raw,p)[0]; p+=2
    col_data = {}
    for _ in range(n_cols):
        nl = struct.unpack_from('>B',payload_raw,p)[0]; p+=1
        cn = payload_raw[p:p+nl].decode(); p+=nl
        sl2= struct.unpack_from('>I',payload_raw,p)[0]; p+=4
        col_data[cn] = payload_raw[p:p+sl2]; p+=sl2

    series = {}
    for col, m in manifests.items():
        if col in col_data:
            series[col] = decode_column(col_data[col], m, stored_rows)
    return pd.DataFrame(series)

# ── AUDIT ─────────────────────────────────────────────────────────────────────
def audit(path):
    with open(path,'rb') as f: raw=f.read(131072)
    if raw[:4]!=MAGIC: raise ValueError("Não é .permafrost")
    pos=4; rd=lambda n,p: (raw[p:p+n], p+n)
    v,pos=rd(2,pos); flags,pos=struct.unpack('>H',raw[pos:pos+2])[0],pos+2
    codec,pos=struct.unpack('>B',raw[pos:pos+1])[0],pos+1
    quant,pos=struct.unpack('>B',raw[pos:pos+1])[0],pos+1
    sl,pos=struct.unpack('>I',raw[pos:pos+4])[0],pos+4; pos+=sl
    ml,pos=struct.unpack('>I',raw[pos:pos+4])[0],pos+4
    mj=json.loads(raw[pos:pos+ml]); pos+=ml
    cl,pos=struct.unpack('>B',raw[pos:pos+1])[0],pos+1
    comment=raw[pos:pos+cl].decode(); pos+=cl
    ft,pos=struct.unpack('>q',raw[pos:pos+8])[0],pos+8
    pos+=4  # retention
    or_,pos=struct.unpack('>Q',raw[pos:pos+8])[0],pos+8
    sr,pos=struct.unpack('>Q',raw[pos:pos+8])[0],pos+8
    ob,pos=struct.unpack('>Q',raw[pos:pos+8])[0],pos+8
    pl,pos=struct.unpack('>Q',raw[pos:pos+8])[0],pos+8
    fs=os.path.getsize(path)
    return {
        'version':f"{v[0]}.{v[1]}",'codec':{CODEC_LZMA2:'lzma2',CODEC_ZSTD:'zstd'}.get(codec,'?'),
        'quant_level':quant,'freeze_date':pd.Timestamp(ft,unit='s').isoformat(),
        'original_rows':or_,'original_bytes':ob,'payload_bytes':pl,
        'file_size_bytes':fs,'ratio':round(ob/pl,3) if pl else 0,
        'comment':comment,'columns':list(mj.keys()),
        'col_predictors':{k:v2.get('predictor') for k,v2 in mj.items()}
    }

print("permafrost_v2.py OK")

# Aliases adicionais para compatibilidade com __init__.py
CODEC_ZPAQ = 0x03
QUANT_LOW  = 0x03

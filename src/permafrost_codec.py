"""
PermafrostCodec v4 — Bug fix: predictor consistente entre chunks
Mudança principal: encode_column_fixed() usa o manifesto já definido
para garantir que todos os chunks usem o mesmo predictor.
"""
import struct, hashlib, io, json, time, os, lzma
import numpy as np, pandas as pd
import zstandard as zstd

MAGIC = b'PRMS'; EOF_MAGIC = b'SMRP'; VERSION = bytes([1, 2])
CODEC_ZSTD=0x01; CODEC_LZMA2=0x02; CODEC_ZPAQ=0x03
QUANT_NONE=0x00; QUANT_HIGH=0x01; QUANT_MEDIUM=0x02; QUANT_LOW=0x03
FLAG_DELTA=0x01; FLAG_QUANTIZE=0x02; FLAG_CHUNKED=0x04
FLAG_PREDICTOR=0x08; FLAG_INDEX=0x10
DEFAULT_CHUNK_ROWS = 10_000

PRED_DELTA='delta_zigzag'; PRED_LAG1='lag1_zigzag'
PRED_CATEGORY='category_u8'; PRED_TS='ts_delta_s'; PRED_RAW='raw_text'

def _sha256(b): return hashlib.sha256(b).digest()
def _zigzag_enc(a): return np.where(a>=0,a*2,-a*2-1).astype(np.uint64)
def _zigzag_dec(a): return np.where(a%2==0,a//2,-(a.astype(np.int64)//2)-1).astype(np.int64)

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
        ts=series.astype('int64')//10**9
        if quant>=QUANT_MEDIUM: ts=(ts//60)*60
        deltas=np.diff(ts.values.astype(np.int64),prepend=0)
        return _zigzag_enc(deltas).astype(np.uint32).tobytes()

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

    return series.astype(str).str.encode('utf-8').str.cat(sep=b'\x00')

# ── DECODE (igual v3) ─────────────────────────────────────────────────────────
def decode_column(data, manifest, n_rows):
    pred=manifest.get('predictor'); scale=manifest.get('scale',1); name=manifest['name']
    if pred==PRED_TS:
        arr=np.frombuffer(data,dtype=np.uint32).astype(np.uint64)
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

def _compress(data,codec):
    if codec==CODEC_LZMA2:
        return lzma.compress(data,format=lzma.FORMAT_XZ,preset=lzma.PRESET_EXTREME|9)
    return zstd.ZstdCompressor(level=19,threads=2).compress(data)

def _decompress(data,codec):
    if codec==CODEC_LZMA2:
        return lzma.decompress(data,format=lzma.FORMAT_XZ)
    return zstd.ZstdDecompressor().decompress(data)

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
def freeze(df:pd.DataFrame, path:str, codec=CODEC_LZMA2, quant=QUANT_NONE,
           chunk_rows=DEFAULT_CHUNK_ROWS, partition_by=None,
           comment="", retention_days=0) -> dict:
    t0=time.time(); orig_bytes=len(df.to_csv(index=False).encode()); orig_rows=len(df)
    flags=FLAG_PREDICTOR|FLAG_DELTA|FLAG_CHUNKED|FLAG_INDEX|(FLAG_QUANTIZE if quant else 0)

    # ── BUG FIX: detectar manifestos UMA VEZ sobre o DataFrame COMPLETO ──────
    manifests={}
    for col in df.columns:
        manifests[col]=_detect_predictor(col,df[col],quant)
        # Para category: usar categorias do DataFrame completo
        if manifests[col]['predictor']==PRED_CATEGORY:
            cats=df[col].astype('category')
            manifests[col]['categories']=list(cats.cat.categories.astype(str))

    # ── Comprimir chunks ─────────────────────────────────────────────────────
    chunk_blobs=[]; index_entries=[]
    for chunk_start in range(0,orig_rows,chunk_rows):
        chunk_end=min(chunk_start+chunk_rows,orig_rows)
        df_chunk=df.iloc[chunk_start:chunk_end].reset_index(drop=True)
        raw_chunk=_encode_chunk(df_chunk,manifests,quant)
        compressed=_compress(raw_chunk,codec); sha=_sha256(compressed)

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

    hdr=b''.join([MAGIC,VERSION,struct.pack('>H',flags),struct.pack('>B',codec),
        struct.pack('>B',quant),struct.pack('>H',len(chunk_blobs)),
        struct.pack('>I',chunk_rows),struct.pack('>I',len(schema_b)),schema_b,
        struct.pack('>I',len(manifest_b)),manifest_b,
        struct.pack('>B',len(comment_b)),comment_b,
        struct.pack('>q',int(time.time())),struct.pack('>I',retention_days),
        struct.pack('>Q',orig_rows),struct.pack('>Q',orig_rows),
        struct.pack('>Q',orig_bytes),])
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
    return {'path':path,'rows':orig_rows,'cols':len(df.columns),
            'n_chunks':len(chunk_blobs),'chunk_rows':chunk_rows,
            'original_mb':round(orig_bytes/1e6,3),'stored_mb':round(stored/1e6,3),
            'ratio':round(orig_bytes/stored,3),
            'reduction_pct':round((1-stored/orig_bytes)*100,2),
            'freeze_s':round(elapsed,3),
            'codec':{CODEC_LZMA2:'lzma2',CODEC_ZSTD:'zstd'}.get(codec,'?'),
            'partition_by':partition_by,'index_entries':len(index_entries)}

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
    hdr_end=p; hdr_sha=rd(32)
    return {'flags':flags,'codec':codec,'quant':quant,'n_chunks':n_chunks,
            'chunk_rows':chunk_rows,'manifests':manifests,'comment':comment,
            'freeze_ts':freeze_ts,'orig_rows':orig_rows,'stored_rows':stored_rows,
            'hdr_end':hdr_end,'hdr_sha_stored':hdr_sha,'payload_start':p}

def _read_sparse_index(raw):
    if raw[-4:]!=EOF_MAGIC: raise ValueError("EOF magic ausente")
    idx_len=struct.unpack('>I',raw[-4-32-4:-4-32])[0]
    idx_sha_stored=raw[-4-32:-4]
    idx_json=raw[-4-32-4-idx_len:-4-32-4]
    if _sha256(idx_json)!=idx_sha_stored: raise ValueError("Sparse index corrompido")
    return json.loads(idx_json)

# ── THAW ─────────────────────────────────────────────────────────────────────
def thaw(path,verify=True,filter=None,row_range=None):
    with open(path,'rb') as f: raw=f.read()
    if raw[-4:]!=EOF_MAGIC: raise ValueError("EOF magic ausente")
    h=_read_header(raw)
    if verify:
        if _sha256(raw[:h['hdr_end']])!=h['hdr_sha_stored']:
            raise ValueError("Header SHA-256 inválido")
    codec=h['codec']; manifests=h['manifests']; orig_rows=h['orig_rows']
    index_entries=_read_sparse_index(raw)

    selected=index_entries
    if filter:
        col_f,val_f=next(iter(filter.items())); val_str=str(val_f)
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
        chunk_raw=_decompress(blob,codec)
        n_rows_chunk=entry['row_end']-entry['row_start']+1
        dfs.append(_parse_chunk(chunk_raw,manifests,n_rows_chunk))

    result=pd.concat(dfs,ignore_index=True) if dfs else pd.DataFrame()
    if filter and len(result):
        col_f,val_f=next(iter(filter.items()))
        if col_f in result.columns:
            result=result[result[col_f].astype(str)==str(val_f)].reset_index(drop=True)
    return result

# ── AUDIT ─────────────────────────────────────────────────────────────────────
def audit(path):
    with open(path,'rb') as f: raw=f.read()
    h=_read_header(raw[:131072]); index=_read_sparse_index(raw)
    codec_name={CODEC_LZMA2:'lzma2',CODEC_ZSTD:'zstd'}.get(h['codec'],'?')
    return {'version':f"{raw[4]}.{raw[5]}",'codec':codec_name,'quant':h['quant'],
            'freeze_date':pd.Timestamp(h['freeze_ts'],unit='s').isoformat(),
            'orig_rows':h['orig_rows'],'n_chunks':h['n_chunks'],
            'chunk_rows':h['chunk_rows'],'file_size_mb':round(os.path.getsize(path)/1e6,3),
            'columns':list(h['manifests'].keys()),'index_entries':index,
            'partition_col':index[0]['part_col'] if index else None,
            'partition_keys':[e['part_key'] for e in index],
            'comment':h['comment']}

print("permafrost_v4.py OK — v1.2 (bug fix: predictor consistente entre chunks)")

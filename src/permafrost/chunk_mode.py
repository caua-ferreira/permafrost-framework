"""
PermafrostCodec — Chunk Mode (streaming freeze) v2 — two-pass correto
"""

from permafrost.codec import (
    _detect_predictor, _encode_chunk, _compress, _decompress,
    _parse_chunk, _read_header, _read_sparse_index,
    _sha256, _pa_schema,
    MAGIC, EOF_MAGIC, VERSION,
    CODEC_LZMA2, CODEC_ZSTD, CODEC_ZPAQ, QUANT_NONE, QUANT_MEDIUM,
    FLAG_DELTA, FLAG_QUANTIZE, FLAG_CHUNKED, FLAG_PREDICTOR, FLAG_INDEX, FLAG_ENCRYPTED,
    decode_column,
)
from permafrost.crypto import resolve_key, encrypt_chunk, decrypt_chunk
import struct, json, time, os
from typing import Callable, Generator, Iterator, Optional
import numpy as np, pandas as pd

DEFAULT_STREAM_CHUNK = 50_000

def freeze_stream(
    iterator: Iterator[pd.DataFrame],
    path: str,
    schema_sample: Optional[pd.DataFrame] = None,
    codec: int = CODEC_LZMA2,
    quant: int = QUANT_NONE,
    partition_by: Optional[str] = None,
    comment: str = "",
    retention_days: int = 0,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    key=None,
) -> dict:
    """Comprime um iterador de DataFrames em um único arquivo ``.permafrost``.

    Usa dois passes para RAM constante proporcional a 1 chunk:
    - **Pass 1**: comprime cada bloco em arquivo de payload temporário.
    - **Pass 2**: constrói o header real com offsets absolutos corretos e
      concatena o payload.

    Args:
        iterator: Iterador que produz ``pd.DataFrame`` em chunks.
        path: Caminho de saída do arquivo ``.permafrost``.
        schema_sample: DataFrame de amostra para detectar preditores; usa o
            primeiro bloco se ``None``.
        codec: Codec de compressão (:data:`CODEC_LZMA2`, :data:`CODEC_ZSTD`
            ou :data:`CODEC_ZPAQ`).
        quant: Nível de quantização (0 = lossless, 1–3 = lossy crescente).
        partition_by: Nome da coluna usada para gerar o sparse index por
            partição.
        comment: Comentário livre embutido no header (máx. 255 bytes).
        retention_days: Dias de retenção sugeridos (apenas informativo,
            não aplicado automaticamente).
        progress_cb: Callback opcional chamado após cada bloco com
            ``(total_rows, n_chunks, stored_mb)``.

    Returns:
        Dicionário de métricas com ``path``, ``rows``, ``cols``, ``n_chunks``,
        ``original_mb``, ``stored_mb``, ``ratio``, ``reduction_pct``,
        ``freeze_s``, ``codec``, ``partition_by``, ``index_entries`` e
        ``mode`` (``"streaming"``).
    """
    raw_key, kms_name, kid = resolve_key(key)

    t0 = time.time()
    flags = FLAG_PREDICTOR | FLAG_DELTA | FLAG_CHUNKED | FLAG_INDEX | (FLAG_QUANTIZE if quant else 0)
    if raw_key is not None:
        flags |= FLAG_ENCRYPTED
    manifests: dict = {}
    index_entries: list = []
    orig_rows = 0
    orig_bytes_est = 0
    first_block = True
    schema_b = b'{}'
    tmp_payload = path + ".payload.tmp"

    # ── PASS 1: gravar chunks comprimidos em temp file ────────────────────────
    with open(tmp_payload, 'wb') as fout:
        cursor = 0

        for block_df in iterator:
            if not isinstance(block_df, pd.DataFrame) or len(block_df) == 0:
                continue
            if first_block:
                sample = schema_sample if schema_sample is not None else block_df
                for col in sample.columns:
                    manifests[col] = _detect_predictor(col, sample[col], quant)
                    if manifests[col]['predictor'] == 'category_u8':
                        cats = sample[col].astype('category')
                        manifests[col]['categories'] = list(cats.cat.categories.astype(str))
                schema_b = _pa_schema(sample)
                first_block = False

            chunk_start = orig_rows
            chunk_end   = orig_rows + len(block_df) - 1
            raw_chunk   = _encode_chunk(block_df.reset_index(drop=True), manifests, quant)
            compressed  = _compress(raw_chunk, codec)
            if raw_key is not None:
                compressed = encrypt_chunk(compressed, raw_key)
            sha         = _sha256(compressed)

            if partition_by and partition_by in block_df.columns:
                pv = sorted(block_df[partition_by].unique().tolist())
                part_key = str(pv[0]) if len(pv) == 1 else f"{pv[0]}-{pv[-1]}"
            else:
                part_key = f"rows_{chunk_start}_{chunk_end}"

            # byte_offset_rel: posição dos DADOS dentro do temp file
            # temp layout: [u32 len][data][sha32] — dados começam em cursor+4
            index_entries.append({
                'chunk_id':        len(index_entries),
                'row_start':       chunk_start,
                'row_end':         chunk_end,
                'part_key':        part_key,
                'part_col':        partition_by or '__rows__',
                '_offset_in_temp': cursor + 4,
                'byte_len':        len(compressed),
                'sha256':          sha.hex(),
            })
            fout.write(struct.pack('>I', len(compressed)))
            fout.write(compressed)
            fout.write(sha)
            cursor += 4 + len(compressed) + 32

            orig_rows      += len(block_df)
            orig_bytes_est += len(raw_chunk)
            if progress_cb:
                progress_cb(orig_rows, len(index_entries), cursor / 1e6)

    # ── PASS 2: construir header real e calcular offsets absolutos ────────────
    manifest_b = json.dumps(manifests, ensure_ascii=False, default=str).encode()
    comment_b  = comment.encode()[:255]

    enc_meta = b''
    if raw_key is not None:
        enc_meta = (struct.pack('>B', len(kms_name)) + kms_name.encode() +
                    struct.pack('>B', len(kid)) + kid.encode())

    hdr = b''.join([
        MAGIC, VERSION,
        struct.pack('>H', flags),
        struct.pack('>B', codec), struct.pack('>B', quant),
        struct.pack('>H', len(index_entries)),
        struct.pack('>I', DEFAULT_STREAM_CHUNK),
        struct.pack('>I', len(schema_b)), schema_b,
        struct.pack('>I', len(manifest_b)), manifest_b,
        struct.pack('>B', len(comment_b)), comment_b,
        struct.pack('>q', int(time.time())),
        struct.pack('>I', retention_days),
        struct.pack('>Q', orig_rows), struct.pack('>Q', orig_rows),
        struct.pack('>Q', orig_bytes_est), enc_meta,
    ])
    hdr_sha = _sha256(hdr)
    payload_start = len(hdr) + 32

    for e in index_entries:
        e['byte_offset'] = payload_start + e.pop('_offset_in_temp')

    index_json = json.dumps(index_entries, ensure_ascii=False).encode()
    index_sha  = _sha256(index_json)

    # ── Escrever arquivo final: header + payload + footer ─────────────────────
    with open(path, 'wb') as fout:
        fout.write(hdr)
        fout.write(hdr_sha)
        with open(tmp_payload, 'rb') as fin:
            while True:
                buf = fin.read(1024 * 1024)
                if not buf:
                    break
                fout.write(buf)
        fout.write(index_json)
        fout.write(struct.pack('>I', len(index_json)))
        fout.write(index_sha)
        fout.write(EOF_MAGIC)

    os.remove(tmp_payload)

    stored = os.path.getsize(path)
    elapsed = time.time() - t0
    return {
        'path':          path,
        'rows':          orig_rows,
        'cols':          len(manifests),
        'n_chunks':      len(index_entries),
        'original_mb':   round(orig_bytes_est / 1e6, 3),
        'stored_mb':     round(stored / 1e6, 3),
        'ratio':         round(orig_bytes_est / stored, 3) if stored else 1,
        'reduction_pct': round((1 - stored / orig_bytes_est) * 100, 2) if orig_bytes_est else 0,
        'freeze_s':      round(elapsed, 3),
        'codec':         {CODEC_LZMA2: 'lzma2', CODEC_ZSTD: 'zstd', CODEC_ZPAQ: 'zpaq'}.get(codec, '?'),
        'partition_by':  partition_by,
        'index_entries': len(index_entries),
        'mode':          'streaming',
    }


def freeze_file(
    input_path: str,
    output_path: Optional[str] = None,
    codec: int = CODEC_LZMA2,
    quant: int = QUANT_NONE,
    chunk_rows: int = DEFAULT_STREAM_CHUNK,
    partition_by: Optional[str] = None,
    comment: str = "",
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    key=None,
) -> dict:
    """Comprime um arquivo grande (CSV ou JSONL) sem carregar tudo na RAM.

    Lê o arquivo em chunks de ``chunk_rows`` linhas e delega para
    :func:`freeze_stream`. Adequado para datasets que excedem a memória disponível.

    Args:
        input_path: Caminho do arquivo de entrada (``.csv``, ``.jsonl`` ou
            ``.ndjson``).
        output_path: Caminho de saída; derivado de ``input_path`` se ``None``.
        codec: Codec de compressão (:data:`CODEC_LZMA2` ou :data:`CODEC_ZSTD`).
        quant: Nível de quantização (0 = lossless).
        chunk_rows: Linhas por chunk (padrão 50.000).
        partition_by: Coluna para particionar o sparse index.
        comment: Comentário livre embutido no header.
        progress_cb: Callback opcional chamado após cada chunk com
            ``(total_rows, n_chunks, stored_mb)``.

    Returns:
        Dicionário de métricas — mesmo formato de :func:`freeze_stream`.

    Raises:
        ValueError: Se a extensão do arquivo não for suportada.
    """
    import json as _json
    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + ".permafrost"
    ext = os.path.splitext(input_path)[1].lower()

    if ext == '.csv':
        schema_sample = pd.read_csv(input_path, nrows=min(1000, chunk_rows))
        def it() -> Iterator[pd.DataFrame]:
            for chunk in pd.read_csv(input_path, chunksize=chunk_rows):
                yield chunk
        return freeze_stream(it(), output_path, schema_sample=schema_sample,
                             codec=codec, quant=quant, partition_by=partition_by,
                             comment=comment, progress_cb=progress_cb, key=key)

    elif ext in ('.jsonl', '.ndjson'):
        from permafrost.schema_detector import SchemaDetector
        det = SchemaDetector(sample_size=500)
        with open(input_path, 'r', encoding='utf-8') as f:
            sample_docs = [_json.loads(l) for l in f if l.strip()][:500]
        schema_df, _, _ = det.flatten(sample_docs)
        def it() -> Iterator[pd.DataFrame]:
            buf: list = []
            with open(input_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        buf.append(_json.loads(line))
                    if len(buf) >= chunk_rows:
                        df_b, _, _ = det.flatten(buf)
                        yield df_b
                        buf = []
            if buf:
                df_b, _, _ = det.flatten(buf)
                yield df_b
        return freeze_stream(it(), output_path, schema_sample=schema_df,
                             codec=codec, quant=quant, partition_by=partition_by,
                             comment=comment, progress_cb=progress_cb, key=key)
    else:
        raise ValueError(f"Formato não suportado: {ext}")


def thaw_iter(
    path: str,
    verify: bool = True,
    filter: Optional[dict] = None,
    batch_size: Optional[int] = None,
    key=None,
    schema_override=None,
) -> Generator[pd.DataFrame, None, None]:
    """Itera sobre chunks de um ``.permafrost`` sem carregar tudo na memória.

    Útil para processar arquivos muito grandes linha por linha, ou para aplicar
    filtros de partição sem descomprimir chunks irrelevantes.

    Args:
        path: Caminho do arquivo ``.permafrost``.
        verify: Valida o SHA-256 de cada chunk quando ``True``.
        filter: Filtro de partição no formato ``{coluna: valor}``; seleciona
            apenas chunks cujo ``part_key`` contenha o valor.
        batch_size: Se definido, agrupa os chunks e emite DataFrames com
            exatamente ``batch_size`` linhas (exceto o último).

    Yields:
        DataFrames pandas restaurados, um por chunk (ou por batch).

    Raises:
        ValueError: Se o arquivo não terminar com o EOF magic correto, se o
            header SHA-256 for inválido, ou se um chunk estiver corrompido.
    """
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[-4:] != EOF_MAGIC:
        raise ValueError("EOF magic ausente")
    h = _read_header(raw)
    if verify:
        if _sha256(raw[:h['hdr_end']]) != h['hdr_sha_stored']:
            raise ValueError("Header SHA-256 inválido")
    codec     = h['codec']
    manifests = h['manifests']

    iter_key = None
    if h['encrypted']:
        iter_key, _, _ = resolve_key(key)
        if iter_key is None:
            raise ValueError(
                "This .permafrost file is encrypted. "
                "Provide key= or set PERMAFROST_KEY env var."
            )

    index     = _read_sparse_index(raw)
    selected  = index
    if filter:
        col_f, val_f = next(iter(filter.items()))
        val_str = str(val_f)
        sel = [e for e in index if e['part_col'] == col_f and val_str in e['part_key']]
        if sel:
            selected = sel

    buf_df: list = []
    buf_rows = 0
    for entry in selected:
        offset  = entry['byte_offset']
        blk_len = entry['byte_len']
        blob    = raw[offset: offset + blk_len]
        sha     = raw[offset + blk_len: offset + blk_len + 32]
        if verify and _sha256(blob).hex() != entry['sha256']:
            raise ValueError(f"Chunk {entry['chunk_id']} corrompido")
        if iter_key is not None:
            blob = decrypt_chunk(blob, iter_key)
        chunk_raw = _decompress(blob, codec)
        n_rows    = entry['row_end'] - entry['row_start'] + 1
        df_c      = _parse_chunk(chunk_raw, manifests, n_rows)

        if schema_override is not None:
            from permafrost.schema_evolution import apply_schema_evolution
            df_c = apply_schema_evolution(df_c, schema_override)
        if batch_size is None:
            yield df_c
        else:
            buf_df.append(df_c)
            buf_rows += len(df_c)
            while buf_rows >= batch_size:
                combined  = pd.concat(buf_df, ignore_index=True)
                yield combined.iloc[:batch_size].copy()
                remainder = combined.iloc[batch_size:].copy()
                buf_df    = [remainder] if len(remainder) > 0 else []
                buf_rows  = len(remainder)

    if buf_df:
        yield pd.concat(buf_df, ignore_index=True)

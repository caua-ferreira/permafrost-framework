# Permafrost Format Specification

**Status:** RFC Draft — v1.3  
**Format version:** 1.3  
**Last updated:** 2026-05-15  
**Reference implementation:** `src/permafrost/codec.py`

---

## 1. Overview

A `.permafrost` file is a self-contained, binary, columnar archive.  
Every file is independently readable — no external catalog, schema registry, or key server is required to inspect or decompress the data (except for encrypted files, which require the encryption key).

### Design goals

| Goal | Mechanism |
|------|-----------|
| Long-term readability | Magic bytes + version field + embedded Arrow schema |
| Bit-rot detection | SHA-256 digest on every chunk + header + sparse index |
| Partial reads without full decompress | Sparse index with per-chunk byte offsets |
| Self-describing | Arrow schema + predictor manifest embedded in header |
| Zero cloud lock-in | StorageAdapter interface — format is storage-agnostic |

---

## 2. File Layout

```
┌──────────────────────────────────────────────────────┐
│  HEADER                                              │
│    MAGIC (4B) + VERSION (2B) + FLAGS (2B)            │
│    CODEC_ID (1B) + QUANT (1B) + N_CHUNKS (2B)        │
│    CHUNK_ROWS (4B)                                   │
│    SCHEMA_LEN (4B) + SCHEMA_BYTES (var)              │
│    MANIFEST_LEN (4B) + MANIFEST_JSON (var)           │
│    COMMENT_LEN (1B) + COMMENT_BYTES (var, ≤255B)    │
│    FREEZE_TIMESTAMP (8B, int64 big-endian)           │
│    RETENTION_DAYS (4B, uint32)                       │
│    ORIG_ROWS (8B, uint64)                            │
│    STORED_ROWS (8B, uint64)                          │
│    ORIG_BYTES (8B, uint64)                           │
│    [ ENC_META (var) — only when FLAG_ENCRYPTED ]     │
├──────────────────────────────────────────────────────┤
│  HEADER_SHA256 (32B)                                 │
├──────────────────────────────────────────────────────┤
│  CHUNK_0                                             │
│    CHUNK_LEN (4B, uint32 big-endian)                 │
│    CHUNK_DATA (CHUNK_LEN bytes, compressed)          │
│    CHUNK_SHA256 (32B)                                │
├──────────────────────────────────────────────────────┤
│  CHUNK_1 ... CHUNK_N                                 │
│    (same layout)                                     │
├──────────────────────────────────────────────────────┤
│  SPARSE_INDEX_JSON (var, UTF-8)                      │
│  INDEX_LEN (4B, uint32 big-endian)                   │
│  INDEX_SHA256 (32B)                                  │
│  EOF_MAGIC (4B) = "SMRP"                            │
└──────────────────────────────────────────────────────┘
```

All multi-byte integers use **big-endian** byte order unless noted otherwise.

---

## 3. Magic Bytes

| Field | Value | Notes |
|-------|-------|-------|
| `MAGIC` (header) | `0x50 0x52 0x4D 0x53` = `"PRMS"` | First 4 bytes of file |
| `EOF_MAGIC` (footer) | `0x53 0x4D 0x52 0x50` = `"SMRP"` | Last 4 bytes of file — PRMS reversed |

The presence of both magic values confirms the file is not truncated.

---

## 4. Header Fields

### 4.1 Fixed-width prefix

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0 | 4B | bytes | `MAGIC` | `b"PRMS"` |
| 4 | 1B | uint8 | `VERSION_MAJOR` | Major version — currently `1` |
| 5 | 1B | uint8 | `VERSION_MINOR` | Minor version — currently `3` |
| 6 | 2B | uint16 | `FLAGS` | Feature bitmask (see §4.2) |
| 8 | 1B | uint8 | `CODEC_ID` | Compression codec (see §4.3) |
| 9 | 1B | uint8 | `QUANT` | Quantization level (see §4.4) |
| 10 | 2B | uint16 | `N_CHUNKS` | Number of chunks in file |
| 12 | 4B | uint32 | `CHUNK_ROWS` | Maximum rows per chunk |

### 4.2 FLAGS bitmask

| Bit | Hex | Name | Meaning |
|-----|-----|------|---------|
| 0 | `0x01` | `FLAG_DELTA` | Delta encoding used |
| 1 | `0x02` | `FLAG_QUANTIZE` | Quantization applied |
| 2 | `0x04` | `FLAG_CHUNKED` | File uses chunked layout |
| 3 | `0x08` | `FLAG_PREDICTOR` | Per-column predictor manifest present |
| 4 | `0x10` | `FLAG_INDEX` | Sparse index present in footer |
| 5 | `0x20` | `FLAG_ENCRYPTED` | AES-256-GCM chunk encryption active |

All flags are OR-combined. Bits 6–15 are reserved and must be zero.

### 4.3 CODEC_ID values

| Value | Name | Algorithm | Best for |
|-------|------|-----------|----------|
| `0x01` | `CODEC_ZSTD` | Zstandard level 19 | Warm storage — fast decompress |
| `0x02` | `CODEC_LZMA2` | LZMA2 extreme (preset 9e) | Cold storage — best ratio |
| `0x03` | `CODEC_ZPAQ` | ZPAQ context mixing | Text-heavy logs |

### 4.4 QUANT values

| Value | Name | Float behavior | Timestamp behavior |
|-------|------|---------------|-------------------|
| `0x00` | `QUANT_NONE` | Lossless (exact) | Lossless (second precision) |
| `0x01` | `QUANT_HIGH` | float64 → float32 | Exact |
| `0x02` | `QUANT_MEDIUM` | Rounded to integer | Truncated to minute |
| `0x03` | `QUANT_LOW` | Rounded to tens | Truncated to hour |

### 4.5 Variable-length header fields

Immediately following the 16-byte fixed prefix:

| Field | Length prefix | Content |
|-------|--------------|---------|
| Arrow schema | `uint32` (4B) | Serialized `pyarrow.Schema` (IPC format) |
| Predictor manifest | `uint32` (4B) | JSON object: column name → manifest dict |
| Comment | `uint8` (1B) | UTF-8 string, max 255 bytes |
| Freeze timestamp | — (8B) | Unix timestamp, `int64` big-endian |
| Retention days | — (4B) | `uint32`; 0 = permanent |
| Original rows | — (8B) | `uint64` — row count before freeze |
| Stored rows | — (8B) | `uint64` — row count in file (same as above, reserved) |
| Original bytes | — (8B) | `uint64` — size of uncompressed CSV representation |

### 4.6 ENC_META (conditional — FLAG_ENCRYPTED only)

When `FLAG_ENCRYPTED` is set, the following fields appear after `ORIG_BYTES`:

| Field | Length prefix | Content |
|-------|--------------|---------|
| KMS name | `uint8` (1B) | e.g., `"local"`, `"aws_kms"`, `"gcp_kms"` |
| Key ID | `uint8` (1B) | Key identifier string |
| EDEK | `uint16` (2B) | Encrypted Data Encryption Key (envelope encryption) |

The DEK itself is never stored in plaintext. The KMS decrypts the EDEK at thaw time to recover the DEK, which then decrypts each chunk.

### 4.7 HEADER_SHA256

Immediately after the header blob (before the first chunk), a 32-byte SHA-256 digest of all header bytes up to this point is written. This digest covers everything from `MAGIC` through the last byte of `ENC_META`.

---

## 5. Chunk Layout

Each chunk is an independently decompressable unit:

```
[CHUNK_LEN: uint32, 4B] [CHUNK_DATA: CHUNK_LEN bytes] [CHUNK_SHA256: 32B]
```

- **CHUNK_LEN** — byte length of the compressed (and optionally encrypted) payload.
- **CHUNK_DATA** — compressed (or encrypted+compressed) columnar data.
- **CHUNK_SHA256** — SHA-256 of `CHUNK_DATA` (computed after encryption if encrypted). Verified before decompression.

### 5.1 Chunk data format (after decompression and decryption)

The decompressed chunk payload is a custom columnar binary format:

```
[N_COLS: uint16, 2B]
for each column:
    [COL_NAME_LEN: uint8, 1B]
    [COL_NAME: COL_NAME_LEN bytes, UTF-8]
    [COL_DATA_LEN: uint32, 4B]
    [COL_DATA: COL_DATA_LEN bytes]  ← predictor-encoded column values
```

### 5.2 Encrypted chunk format

When `FLAG_ENCRYPTED` is set, `CHUNK_DATA` contains:

```
[NONCE: 12B] [CIPHERTEXT: N bytes] [GCM_TAG: 16B]
```

The plaintext (CIPHERTEXT with GCM_TAG) is verified by AES-256-GCM before decompression. The nonce is unique per chunk (randomly generated at freeze time).

---

## 6. Column Predictors

Each column has a predictor assigned in the manifest. The predictor transforms column values before compression to maximize codec efficiency.

### 6.1 Predictor names

| Name | Field | Encoding |
|------|-------|----------|
| `delta_zigzag` | Integer IDs, monotonic integers | Successive differences → zigzag-encoded uint32 array |
| `lag1_zigzag` | Float prices, metrics | Lag-1 residuals × scale → zigzag-encoded uint32 array |
| `ts_delta_s` | Datetime columns | Unix seconds differences → zigzag-encoded uint64 array |
| `category_u8` | Low-cardinality strings (≤256 distinct) | Category index → uint8 array |
| `raw_text` | High-cardinality strings | NUL-delimited UTF-8 |
| `float32_quantized` | Floats with acceptable precision loss | float64 → float32 → raw bytes |
| `float16_quantized` | Floats with high precision loss acceptable | float64 → float16 → raw bytes |
| `json_schema_v2` | Columns with ≥70% JSON dict values | Key compression via shared integer dict + NUL-delimited JSON |

### 6.2 Zigzag encoding

Signed integers are mapped to unsigned integers before being stored, allowing small negative values to compress well:

```
encode(n) = 2n       if n ≥ 0
          = -2n - 1  if n < 0

decode(n) = n/2      if n is even
          = -(n+1)/2 if n is odd
```

### 6.3 Predictor manifest JSON schema

The manifest is a JSON object where each key is a column name and the value is a manifest dict:

```json
{
  "preco": {
    "name": "preco",
    "dtype": "float64",
    "predictor": "lag1_zigzag",
    "scale": 100
  },
  "categoria": {
    "name": "categoria",
    "dtype": "object",
    "predictor": "category_u8",
    "categories": ["Ativo", "Cancelado", "Pendente"]
  },
  "data": {
    "name": "data",
    "dtype": "datetime64[ns]",
    "predictor": "ts_delta_s",
    "scale": 1
  },
  "payload": {
    "name": "payload",
    "dtype": "object",
    "predictor": "json_schema_v2",
    "key_dict": ["id", "nome", "valor"]
  },
  "score": {
    "name": "score",
    "dtype": "float64",
    "predictor": "float32_quantized",
    "precision_bits": 32,
    "max_abs_error": 0.000122,
    "max_rel_error": 1.1920929e-07
  }
}
```

---

## 7. Footer — Sparse Index

The sparse index enables partial reads without decompressing the entire file.

### 7.1 Layout

```
[SPARSE_INDEX_JSON: var, UTF-8]
[INDEX_LEN: uint32, 4B]         ← byte length of SPARSE_INDEX_JSON
[INDEX_SHA256: 32B]             ← SHA-256 of SPARSE_INDEX_JSON
[EOF_MAGIC: 4B] = "SMRP"
```

To read the sparse index without downloading the full file, implementations should:

1. Fetch the last `4 + 32 + 4 = 40` bytes.
2. Verify EOF_MAGIC (`SMRP`).
3. Read `INDEX_LEN` from bytes `[36:40]` (before EOF_MAGIC — adjust for actual offsets by reading `[0:4]` then seeking).
4. Fetch `INDEX_LEN` bytes ending at `INDEX_SHA256` start.
5. Verify `INDEX_SHA256`.

Cloud storage adapters use HTTP Range requests for step 4 to avoid full downloads.

### 7.2 Sparse index JSON schema

```json
[
  {
    "chunk_id":   0,
    "row_start":  0,
    "row_end":    9999,
    "part_key":   "2022",
    "part_col":   "ano",
    "byte_offset": 2048,
    "byte_len":   14392,
    "sha256":     "a1b2c3..."
  },
  {
    "chunk_id":   1,
    "row_start":  10000,
    "row_end":    19999,
    "part_key":   "2023",
    "part_col":   "ano",
    "byte_offset": 16440,
    "byte_len":   13871,
    "sha256":     "d4e5f6..."
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `chunk_id` | int | 0-based chunk index |
| `row_start` | int | First row index in this chunk (inclusive) |
| `row_end` | int | Last row index in this chunk (inclusive) |
| `part_key` | str | Partition value (e.g., `"2022"`) or `"rows_0_9999"` if no partition column |
| `part_col` | str | Partition column name, or `"__rows__"` |
| `byte_offset` | int | Absolute byte offset of `CHUNK_DATA` start (after CHUNK_LEN prefix) |
| `byte_len` | int | Byte length of `CHUNK_DATA` |
| `sha256` | str | Hex SHA-256 of `CHUNK_DATA` |

---

## 8. Audit (zero-decompress inspection)

An implementation can inspect a `.permafrost` file without any decompression by:

1. Reading the first `~2KB` — sufficient for any real-world header.
2. Verifying `MAGIC == b"PRMS"`.
3. Parsing fixed prefix fields (version, flags, codec, quant, n_chunks).
4. Parsing the variable-length fields to extract schema, manifest, and comment.
5. Verifying `HEADER_SHA256`.
6. Reading the sparse index from the footer (steps in §7.1).

The `audit()` API returns all of this without touching chunk data.

---

## 9. Version History

| Version | Change |
|---------|--------|
| 1.0 | Initial format: header, payload, SHA-256 per chunk |
| 1.1 | Sparse Index footer + independent chunks (`FLAG_CHUNKED`, `FLAG_INDEX`) |
| 1.2 | Predictor manifest fixed to full DataFrame at freeze time (cross-chunk consistency) |
| 1.3 | AES-256-GCM encryption per chunk (`FLAG_ENCRYPTED`, `ENC_META`, envelope DEK) |

### Compatibility guarantees

- Files v1.x are readable by any implementation that supports v1.y where y ≥ x.
- Files from different minor versions can be distinguished by the `VERSION_MINOR` byte at offset 5.
- Implementations **must** reject files with `VERSION_MAJOR` > their own supported major version.
- The `EOF_MAGIC` check (`SMRP`) allows truncation detection without reading the full file.

---

## 10. Conformance Checklist

An implementation **must**:

- [ ] Verify `MAGIC == b"PRMS"` before reading any other field
- [ ] Verify `EOF_MAGIC == b"SMRP"` before declaring the file intact
- [ ] Verify `HEADER_SHA256` before using any header field
- [ ] Verify each `CHUNK_SHA256` **before** decompressing that chunk
- [ ] Verify `INDEX_SHA256` before using the sparse index
- [ ] Use the `manifests` from the header (not re-detect) when decoding chunks

An implementation **should**:

- [ ] Implement range-request audit (read only header + footer for remote files)
- [ ] Support `FLAG_ENCRYPTED` (decrypt before decompress; verify GCM tag)
- [ ] Expose `VERSION_MAJOR` and `VERSION_MINOR` in the audit API

An implementation **may**:

- [ ] Implement `CODEC_ZPAQ` — it requires the `zpaq` system binary
- [ ] Implement the cluster protocol (PermafrostMaster / PermafrostWorker)

---

## 11. Reference Implementation

The canonical implementation is in Python:

- **Encode/decode:** `src/permafrost/codec.py` — `freeze()`, `thaw()`, `audit()`
- **Encryption:** `src/permafrost/crypto.py`
- **Streaming (chunk mode):** `src/permafrost/chunk_mode.py`
- **Cloud range requests:** `src/permafrost/storage.py`

Test vectors with known SHA-256 digests are available in `tests/test_codec.py`.

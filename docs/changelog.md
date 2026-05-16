# Changelog

All notable changes to the Permafrost Data Framework are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Versioning follows [Semantic Versioning 2.0.0](https://semver.org/).

---

## [0.8.0] — 2026-05-15

### Added
- **Helm Chart + Kubernetes Operator** (`charts/permafrost/`, `src/permafrost/operator.py`)
  - CRD `PermafrostJob` (apiVersion: `permafrost.io/v1alpha1`)
  - Operator lifecycle: Pending → Running → Completed | Failed
  - kopf-based controller with 15s polling, HPA, RBAC
- **Codec Auto-Selector** (`src/permafrost/auto_codec.py`)
  - `freeze(df, path, codec="auto")` — selects optimal codec + quant from 1000-row sample
  - `DataProfile`, `auto_select()`, `profile_dataframe()` exported from `permafrost`
  - Returns `auto_reason` key in freeze metrics
- **CLI Binary Standalone** — PyInstaller-built zero-dependency binary
  - Works without Python installed — guaranteed readability through 2040
  - Published on GitHub Releases; install via `curl | sh` or PowerShell
  - Install scripts: `scripts/install.sh` (Unix), `scripts/install.ps1` (Windows)
- **RBAC** (`src/permafrost/rbac.py`)
  - JWT-based auth with `can_freeze`, `can_thaw`, `namespace` claims
  - `RBACManager`, `ClusterUser`, `generate_token`, `validate_token` exported
  - `permafrost cluster add-user` / `list-users` / `remove-user` CLI commands
- **Predictor `json_schema_v2`** — NoSQL key compression
  - Detects columns with ≥70% JSON dict values automatically
  - Shared key dict per chunk reduces pre-compression bytes for long-key schemas
- **`PermafrostContext`** — unified high-level API
  - `ctx.freeze()`, `ctx.thaw()`, `ctx.audit()`, `ctx.list()` — catalog + storage + cluster
  - `ctx.freeze_async()` + `ctx.wait()` — async cluster submission
  - Full catalog delegation: `search()`, `cost_report()`, `integrity_check()`, `stats()`, `sql()`
  - Context manager support (`with PermafrostContext(...) as ctx`)
- Test suite expanded to **93% coverage** (385 passed, 5 skipped in CI)

---

## [0.7.0] — 2026-05-14

### Added
- **Encryption at Rest** — AES-256-GCM per-chunk encryption
  - `freeze(df, path, key=b"32-bytes...")` or via env `PERMAFROST_KEY`
  - Envelope encryption: `LocalKeyProvider`, `AWSKMSProvider`, `GCPKMSProvider`
  - EDEK stored in header `ENC_META`; `thaw()` decrypts transparently
  - `audit()` reports `encrypted=True`, `kms`, `edek_size`
  - Format: nonce (12B) + ciphertext + GCM tag (16B) per chunk
- **Predictor `float32_quantized` / `float16_quantized`** — lossy high-performance
  - `PRED_FLOAT32` / `PRED_FLOAT16` constants
  - `precision_bits`, `max_abs_error`, `max_rel_error` per column in `audit()`
  - Integrated with `QUANT_HIGH` → float32, `QUANT_LOW` → float16
- **Schema Evolution** (`src/permafrost/schema_evolution.py`)
  - `thaw(schema_override=pa.Schema)` — auto-cast to newer schema
  - `schema_diff()` — diff between stored and target schema
  - `apply_schema_evolution()` exported for standalone use
  - Rules: new columns → null, removed columns → ignored, compatible types → cast
- **Resumable Uploads** in StorageAdapter
  - `upload_resumable()` on `LocalAdapter` and `S3Adapter`
  - State file `.upload_state` indexed by `src_mtime + src_size + remote_uri`
  - Exponential backoff retry (3 attempts, up to 60s)
  - S3 Multipart Upload with ETag persistence; `ResumableUploadError` exported

---

## [0.6.0] — 2026-05-12

### Added
- **`PermafrostCatalog` thread safety** (LIM-006) — lock on all DuckDB operations
- **Streaming chunk mode fixes**
  - `freeze_file()` and `freeze_stream()` use correct two-pass: pass 1 to temp, pass 2 assembles
- **Timestamp predictor fix** (LIM-001) — `ts_delta_s` now handles non-monotonic timestamps
  with a `UserWarning` instead of producing corrupted output
- Windows CI compatibility fixes — path handling, encoding fallbacks

---

## [0.5.2] — 2026-05-10

### Added
- **Docker Hub** — `permafrost-master` and `permafrost-worker` images
  - Multi-arch: `linux/amd64` + `linux/arm64`
  - `docker-compose up --scale worker=4` — cluster ready without local build
- **`python -m permafrost`** entrypoint for containers
  - `python -m permafrost master [--host] [--port]`
  - `python -m permafrost worker --master URL [--host] [--port]`
- GitHub Actions `docker.yml` — auto build+push on `v*` tags

---

## [0.5.1] — 2026-05-09

### Added
- **`CODEC_ZPAQ`** (`0x03`) — ZPAQ context mixing via system `zpaq` binary
  - Best for text-heavy logs; up to 27% smaller than LZMA2 on prose
  - Requires: `apt install zpaq` | `brew install zpaq`
- Google-style docstrings on all public functions (mkdocstrings auto API reference)
- Full type hints on `freeze()`, `thaw()`, `audit()`

### Fixed
- `audit()` codec name map now includes `"zpaq"`

---

## [0.5.0] — 2026-05-08

### Added
- **StorageAdapters**: `LocalAdapter`, `S3Adapter`, `GCSAdapter`, `AzureAdapter`
- `freeze_to()`, `thaw_from()`, `audit_remote()` — cloud in one line
- S3 HTTP range requests — `audit_remote()` without full download
- **PermafrostCluster**: `PermafrostMaster` + `PermafrostWorker` + `PermafrostClient` via FastAPI
  - Round-robin scheduling, 3× auto-retry, heartbeat, cancellation
  - Multiple parallel jobs with shared workers
  - Docker Compose for local cluster

### Fixed
- Timestamp bug: `datetime64[us].astype('int64')` returns microseconds in pandas 2.0+.
  Fixed by using `pd.Timedelta('1s')` for resolution-agnostic division.

---

## [0.4.0] — 2026-05-06

### Added
- **SchemaDetector** — automatic schema detection for CSV, JSONL, MongoDB, social media
  - Flatten: scalar fields → columns with predictors; arrays/nested → JSON string
- **Chunk Mode**: `freeze_stream()`, `freeze_file()`, `thaw_iter()` — constant-RAM datasets
- CLI: `permafrost freeze/thaw/audit/verify/catalog` (Typer + Rich)

---

## [0.3.0] — 2026-05-04

### Added
- **PermafrostCatalog** (DuckDB) — centralized index of `.permafrost` files
  - `register()`, `register_dir()`, `search()`, `thaw()`, `cost_report()`, `integrity_check()`, `sql()`
  - Tables: `datasets` + `chunks` (mirrors sparse index)

### Fixed
- **Critical predictor bug**: encoder re-detected predictor per chunk, causing inconsistency
  when cardinality varied. Fixed: manifest detected once over full DataFrame and frozen.

---

## [0.2.0] — 2026-05-02

### Added
- **Sparse Index** — footer-based index (Parquet-inspired)
- Selective thaw: `thaw(filter={"ano": 2021})` reads 12–31% of file
- `thaw(row_range=(start, end))` for row range access
- `audit()` reads header + sparse index without decompressing any chunk
- Format v1.1: flags `FLAG_CHUNKED`, `FLAG_INDEX`

---

## [0.1.0] — 2026-05-01

### Added
- Format `.permafrost` v1.0: magic `PRMS`, dual SHA-256 (header + payload), embedded Arrow schema
- 5 column predictors: `delta_zigzag`, `lag1_zigzag`, `ts_delta_s`, `category_u8`, `raw_text`
- Codecs: Zstd L19 (`CODEC_ZSTD`) and LZMA2 extreme (`CODEC_LZMA2`)
- Quantization levels: `QUANT_NONE`, `QUANT_HIGH`, `QUANT_MEDIUM`, `QUANT_LOW`
- `freeze()`, `thaw()`, `audit()` — public API
- Bit-rot detection on header and payload before any decompression

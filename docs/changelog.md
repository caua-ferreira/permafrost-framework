# Changelog

All notable changes to the Permafrost Data Framework are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Versioning follows [Semantic Versioning 2.0.0](https://semver.org/).

---

## [1.3.1] — 2026-05-18

### Added
- **`PermafrostCatalogServer`** — REST API (FastAPI) sobre o `PermafrostCatalog`
  - `PermafrostCatalogServer(db_path, backend=None)` — wraps catalog in an HTTP server
  - 12 endpoints: `GET /health`, `POST /datasets/register`, `POST /datasets/register_dir`,
    `GET /datasets`, `GET /datasets/{name}`, `GET /datasets/{name}/versions`,
    `GET /datasets/{name}/chunks`, `GET /datasets/{name}/integrity`,
    `DELETE /datasets/{name}`, `GET /stats`, `GET /cost_report`, `POST /sql`, `GET /search`
  - Interactive Swagger UI at `/docs`, ReDoc at `/redoc`
  - All DataFrames serialized to JSON with NaN → null handling
  - Exported as `pf.PermafrostCatalogServer`
- **`permafrost catalog serve`** CLI command
  - `permafrost catalog serve --db catalog.db --host 0.0.0.0 --port 8800 --log-level info`
  - Prints URL and docs link on startup
- 37 new integration tests in `tests/test_catalog_server.py`

---

## [1.3.0] — 2026-05-18

### Added
- **Per-column codec** (`per_column_codec`, `codec_profile`)
  - Assign a different codec to each column: `freeze(df, path, per_column_codec={"col": "lzma2"})`
  - `codec_profile` presets: `balanced` / `max_compression` / `max_speed` / `auto`
  - `auto` profile samples 1000 rows per column and picks the best codec automatically
- **Catalog remote backends** (`catalog_backends.py`)
  - `CatalogBackend` ABC with `resolve_path()` and `upload()` interface
  - `LocalCatalogBackend` — default, resolves `os.path.abspath`
  - `S3CatalogBackend` — ETag-based cache validation + LRU eviction by byte budget
  - `GCSCatalogBackend` / `AzureCatalogBackend` — TTL-based cache
  - `PermafrostCatalog(db, backend=...)` — inject backend at construction or via `.configure()`
- **Catalog versioning**
  - `catalog.register(path, name="ds", version="v2024")` — stores version string per entry
  - `catalog.versions(name)` — returns a DataFrame with full version history
  - `catalog.unfreeze(name, version="v2024")` — selects a specific version
- **Remote query backend** — `pf.set_query_backend(backend)` resolves `s3://` aliases before querying
- **OpenSSF Scorecard improvements**
  - All GitHub Actions pinned to full SHAs (Pinned-Dependencies)
  - Explicit `permissions: read-all` on all workflows (Token-Permissions)
  - CodeQL SAST workflow added (SAST check)
  - `SECURITY.md` — vulnerability reporting policy (Security-Policy)
  - `dependabot.yml` — weekly pip + actions updates (Dependency-Update-Tool)
- **PyPI downloads badge** — migrated from rate-limited `img.shields.io` to `static.pepy.tech`

### Changed
- CI workflows chain: `docker.yml`, `publish.yml`, `release.yml` now trigger via `workflow_run` only after `tests.yml` passes
- Test suite: **1261 passed**, 9 skipped, **93% coverage**

---

## [1.2.1] — 2026-05-18

### Added
- **Diff engine** — `pf.diff(path_a, path_b)` reports added / removed / changed rows at chunk resolution
- **SQL query engine** — `pf.query(sql)` runs DuckDB SQL over registered `.permafrost` aliases
  - `pf.register(alias, path)` / `pf.unregister(alias)` / `pf.registered()`
  - Supports local paths and `s3://` URIs (with `set_query_backend`)
- **Schema Evolution** — `pf.Schema`, `pf.Field`, `schema_diff()`
  - `thaw(schema_override=target_schema)` auto-casts to newer schema
  - New columns → fill with null; removed columns → silently dropped; compatible types → cast

---

## [1.2.0] — 2026-05-17

### Added
- **`primary_key`** parameter on `freeze()` — declares the identity column for diff operations
- **`.pf` alias** — `import permafrost as pf` is now the canonical import style; all docs updated
- **`output_format`** on `unfreeze()` — `"xlsx"` and `"csv"` output without loading into memory
- **`partition_by` multi-column** — `freeze(df, path, partition_by=["year", "region"])`
- **`pf.append(df, path)`** — incremental writes; appends chunks without re-freezing the whole file

---

## [1.1.4] — 2026-05-16

### Fixed
- `audit()` — `lossy_columns` and `edek_size` now included in return dict
- `audit()` — `stored_schema` (Arrow schema as JSON string) added to return dict
- CI — `openpyxl` added to dev dependencies for xlsx export tests

---

## [1.1.3] — 2026-05-16

### Fixed
- Cluster fault-tolerance fixtures now use dynamic port allocation — eliminates `WinError 10048` when running parallel test sessions

---

## [1.1.2] — 2026-05-15

### Added
- CI ordering — `docker.yml`, `publish.yml`, `release.yml` chain via `workflow_run` after `tests.yml`

---

## [1.1.1] — 2026-05-15

### Added
- Multi-language READMEs (pt-BR, es, fr, zh-CN, ar, hi) with language selector
- `CONTRIBUTING.md` — development setup, PR guidelines, commit conventions

---

## [1.0.0] — 2026-05-15

First stable release. All v1.0 milestones complete.

### Added
- **`PermafrostContext`** — unified high-level API (catalog + storage + cluster)
- **Format Spec RFC draft** — `docs/format-spec.md`, byte-level v1.3 specification
- **SDK Stability Policy** — semantic versioning, deprecation process, public API contract
- **MkDocs documentation** — full API reference, Getting Started, User Guide, Changelog

### Changed
- `Development Status` classifier: `3 - Alpha` → `5 - Production/Stable`
- Version bump: `0.8.0` → `1.0.0`

### Notes
- All features from 0.1.0 through 0.8.0 are included and stable
- `.permafrost` format v1.3 — backward-compatible with all v1.x files
- Test suite: 385 passed, 5 skipped, 93% coverage

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
  - `ctx.freeze()`, `ctx.unfreeze()`, `ctx.audit()`, `ctx.list()` — catalog + storage + cluster
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
- **Chunk Mode**: `freeze_stream()`, `freeze_file()`, `peek()` — constant-RAM datasets
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

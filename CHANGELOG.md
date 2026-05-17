# Changelog

All notable changes to Permafrost Framework are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.1.0] - 2026-05-17

### Changed (breaking — old names kept as deprecated aliases until v2.0)
- `thaw()` renamed to `unfreeze()` — clearer: the inverse of `freeze()`
- `thaw_iter()` renamed to `peek()` — reads fragments/chunks without full load
- `PermafrostCatalog.thaw()` → `PermafrostCatalog.unfreeze()`
- `PermafrostContext.thaw()` → `PermafrostContext.unfreeze()`
- CLI command `permafrost thaw` → `permafrost unfreeze`

### Deprecated
- `thaw()`, `thaw_iter()`, `cat.thaw()`, `ctx.thaw()`, `permafrost thaw` — all emit
  `DeprecationWarning` and will be removed in v2.0

### Added
- `freeze_append()` — append rows to existing `.permafrost` without re-freezing
- Range filter: `unfreeze(path, filter={"ano": (2021, 2022)})` for partition ranges
- Polars support: `freeze()` accepts `polars.DataFrame`; `unfreeze(engine="polars")`

---

All notable changes to Permafrost Framework are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.1] - 2026-05-15

### Added
- English README and full documentation translated to English
- Encryption guide (`docs/user-guide/encryption.md`)
- Section 7 "Encryption at rest (AES-256-GCM)" in Getting Started

### Changed
- PyPI package now ships English README

---

## [1.0.0] - 2026-05-01

### Added
- `PermafrostContext` — unified API (catalog + storage + cluster in one object)
- AES-256-GCM encryption at rest, per-chunk with unique nonce
- `KeyProvider` interface: `LocalKeyProvider`, `AWSKMSProvider`, `GCPKMSProvider`
- `PermafrostCatalog` (DuckDB-backed) made thread-safe
- Cloud storage adapters: S3, GCS, Azure with resumable upload
- `PermafrostMaster` / `PermafrostWorker` / `PermafrostClient` cluster API
- RBAC: `RBACManager`, `ClusterUser`, `generate_token`, `validate_token`
- Auto codec selector (`CODEC_AUTO`, `profile_dataframe`, `auto_select`)
- Schema evolution: `apply_schema_evolution`, `schema_diff`
- Spark DataSource API v2 (optional, requires PySpark)
- Standalone binary releases for Linux, macOS, Windows

### Fixed
- `PermafrostCatalog` thread-safety (LIM-006)
- `ts_delta_s` with unsorted timestamps (LIM-001)
- Windows path compatibility

---

## [0.7.1] - 2026-04-10

### Fixed
- PyPI sidebar URLs

---

## [0.7.0] - 2026-04-05

### Added
- OpenSSF Scorecard badge and CI job
- Codecov integration
- Python 3.13 support in CI matrix

---

## [0.6.4] - 2026-03-20

### Added
- Docker Compose examples and notebook execution
- `Dockerfile.master` and `Dockerfile.worker`

---

## [0.6.3] - 2026-03-15

### Fixed
- Minor compatibility fixes

---

## [0.6.2] - 2026-03-10

### Fixed
- CI test badge (shields.io)

---

## [0.6.1] - 2026-03-05

### Added
- 197 tests across 5 new test suites
- Bug fixes identified by test suite

---

## [0.6.0] - 2026-03-01

### Added
- Initial public release
- `freeze` / `thaw` / `audit` core API
- ZSTD, LZMA2, ZPAQ codecs
- Sparse index for selective decompression (`partition_by`)
- Quantization levels (QUANT_NONE / HIGH / MEDIUM / LOW)
- Column predictors: DELTA, LAG1, CATEGORY, TS, FLOAT32, FLOAT16, JSON_V2
- `freeze_file` / `freeze_stream` / `thaw_iter` for streaming (datasets > RAM)
- `SchemaDetector` for SQL/NoSQL/JSONL

# ❄️ Permafrost Framework

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/permafrost-framework/)
[![Downloads](https://static.pepy.tech/badge/permafrost-framework/month)](https://pepy.tech/project/permafrost-framework)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)
[![Coverage](https://img.shields.io/codecov/c/github/caua-ferreira/permafrost-framework?logo=codecov)](https://codecov.io/gh/caua-ferreira/permafrost-framework)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/caua-ferreira/permafrost-framework/badge)](https://securityscorecards.dev/viewer/?uri=github.com/caua-ferreira/permafrost-framework)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs-00D4FF)](https://caua-ferreira.github.io/permafrost-framework)


**Distributed intelligent compression platform for long-term digital archiving.**

*210 million rows: Permafrost + LZMA2 = 3.03 GB vs CSV = 16.35 GB (5.4×) — nearly 2× better than Parquet. Query a single year from 5 years of data: 42M rows read, only 20% of the file touched.*

🌐 **English** · [Português (BR)](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.pt-BR.md) · [Español](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.es.md) · [Français](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.fr.md) · [中文](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.zh-CN.md) · [العربية](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.ar.md) · [हिन्दी](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.hi.md)

[Documentation](https://caua-ferreira.github.io/permafrost-framework) · [Quick Start](https://github.com/caua-ferreira/permafrost-framework#quick-start) · [Benchmarks](https://github.com/caua-ferreira/permafrost-framework#benchmarks) · [API](https://github.com/caua-ferreira/permafrost-framework#api-reference) · [Contributing](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md)

</div>

---

## What is Permafrost?

Corporate historical data — CSVs, JSONL, MongoDB dumps — sits in cold storage (S3 Glacier, Azure Archive) for years at a high cost. The problem: if you need data from a single month in a 10 GB file, you have to decompress **everything**.

Permafrost solves this with two mechanisms:

1. **Column predictors** — semantically transforms data before compression (delta, zigzag, timestamps, categories), achieving ratios far above plain LZMA2
2. **Sparse index** — an index embedded in the file that points to the exact byte offset of each chunk, enabling selective reads via HTTP Range Requests without downloading the entire file

```
210,000,000 rows × 13 columns — real benchmark measured locally:

Raw CSV:                  16.35 GB  (1.00×)
Parquet + Snappy:          5.89 GB  (2.78×)   freeze:  8.9 min
CSV + plain LZMA2 (p9):   ~3.80 GB  (~4.3×)   freeze:  ~7 h  ⚠️ impractical
Permafrost + ZSTD:         3.25 GB  (5.03×)   freeze: 77.7 min
Permafrost + LZMA2:        3.03 GB  (5.40×)   freeze: 93.5 min   ← nearly 2× better than Parquet

Query only year 2022 → 42M rows in 5.7 min — read 20% of the file, 80% never touched
```

---

## Features

- **High compression** — column predictors (delta_zigzag, lag1_zigzag, ts_delta_s, category_u8, raw_text) before Zstd / LZMA2 / ZPAQ
- **Per-column codec** — assign different codecs to each column; `codec_profile` presets (balanced / max_compression / max_speed / auto)
- **Selective reads** — embedded sparse index enables `filter={"year": 2023}` without decompressing the rest
- **SQL query engine** — `pf.query("SELECT … FROM alias")` via DuckDB; register aliases with `pf.register()`
- **Diff engine** — `pf.diff(path_a, path_b)` reports added / removed / changed rows at chunk level
- **Incremental writes** — `pf.append(df, path)` adds data without re-freezing the whole file
- **Schema evolution** — `pf.Schema` / `pf.Field` contract; auto-cast on thaw, `schema_diff()` for migration planning
- **Guaranteed integrity** — SHA-256 per chunk, verified before any decompression
- **Self-describing** — full Arrow schema embedded in the file; readable in 2040 without external documentation
- **Cloud-native** — native support for S3, Google Cloud Storage and Azure Blob Storage with HTTP Range Requests
- **Remote catalog backends** — `S3CatalogBackend`, `GCSCatalogBackend`, `AzureCatalogBackend` with ETag cache and LRU eviction
- **DuckDB Catalog** — metadata search across hundreds of remote files; versioned datasets with `version=` and `.versions()`
- **Streaming** — process datasets larger than RAM with `freeze_file()` and `peek()`
- **Distributed cluster** — Master + Workers via FastAPI; processes 1 TB in parallel with N workers
- **Spark DataSource v2** — native integration with PySpark 4.0+ with sparse index pushdown
- **Full CLI** — `permafrost freeze / thaw / audit / verify / catalog` with rich output

---

## Installation

```bash
# Basic installation
pip install permafrost-framework

# With AWS S3 support
pip install "permafrost-framework[s3]"

# With Google Cloud Storage support
pip install "permafrost-framework[gcs]"

# With Azure Blob Storage support
pip install "permafrost-framework[azure]"

# All cloud providers
pip install "permafrost-framework[all-cloud]"

# With Apache Spark
pip install "permafrost-framework[spark]"
```

**Requirements:** Python 3.10+

---

## Quick Start

### Basic Freeze and Thaw

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("sales_history.csv")

# Compress — returns metrics
metrics = pf.freeze(df, "sales.permafrost", codec=pf.CODEC_LZMA2, partition_by="year")
print(f"Ratio: {metrics['ratio']:.2f}×  |  {metrics['original_mb']:.1f} MB → {metrics['stored_mb']:.1f} MB")
# Ratio: 8.37×  |  5.85 MB → 0.68 MB

# Decompress everything
df_back = pf.unfreeze("sales.permafrost", verify=True)

# Decompress only 2023 — reads only the chunks for that year
df_2023 = pf.unfreeze("sales.permafrost", filter={"year": 2023})
```

### Streaming (datasets larger than RAM)

```python
# Freeze a large file without loading it into memory
pf.freeze_file("100gb.csv", "output.permafrost", chunk_rows=50_000)

# Iterative thaw in batches
for batch_df in pf.peek("output.permafrost", batch_size=50_000):
    process(batch_df)
```

### Cloud (S3, GCS, Azure)

```python
# Upload directly to S3
pf.freeze_to(df, "s3://my-bucket/data/sales.permafrost")

# Selective read from S3 via HTTP Range Request — does not download the entire file
df_2023 = pf.thaw_from("s3://my-bucket/data/sales.permafrost", filter={"year": 2023})

# Remote audit without downloading anything
info = pf.audit_remote("s3://my-bucket/data/sales.permafrost")
```

### Per-column codec

```python
# Assign a different codec to each column
metrics = pf.freeze(df, "sales.permafrost", per_column_codec={
    "id":    "zstd",    # fast for integer keys
    "notes": "lzma2",  # better ratio for free text
    "value": "zstd",
})

# Or use a preset profile
metrics = pf.freeze(df, "sales.permafrost", codec_profile="max_compression")
# profiles: "balanced" | "max_compression" | "max_speed" | "auto"
```

### SQL query engine

```python
pf.register("sales", "sales.permafrost")
pf.register("returns", "returns.permafrost")

df = pf.query("""
    SELECT s.year, SUM(s.value) - SUM(r.value) AS net
    FROM sales s
    JOIN returns r ON s.year = r.year
    GROUP BY s.year
    ORDER BY s.year
""")
```

### Catalog — search across multiple files

```python
cat = pf.PermafrostCatalog("catalog.db")
cat.register_dir("s3://my-bucket/cold/")   # indexes metadata without downloading

# Search by name, codec, lossless
results = cat.search(name="sales", lossless_only=True)

# Register with version and retrieve by version
cat.register("sales_2024.permafrost", name="sales", version="v2024")
cat.register("sales_2023.permafrost", name="sales", version="v2023")
df_v2023 = cat.unfreeze("sales", version="v2023")
history  = cat.versions("sales")   # DataFrame with all registered versions

# Remote backend — resolve S3/GCS/Azure paths transparently
from permafrost import S3CatalogBackend
cat = pf.PermafrostCatalog("catalog.db", backend=S3CatalogBackend(bucket="my-bucket"))
cat.register("s3://my-bucket/cold/sales.permafrost", name="sales")

# Estimated cost report for Glacier Deep Archive
cat.cost_report("glacier_deep")

# Bulk integrity check
cat.integrity_check()
```

### Diff engine

```python
report = pf.diff("sales_v1.permafrost", "sales_v2.permafrost")
print(report)
# {"added_rows": 5000, "removed_rows": 200, "changed_rows": 47, "chunks_changed": 3}
```

### Incremental writes

```python
pf.freeze(df_2023, "history.permafrost")
pf.append(df_2024, "history.permafrost")   # adds without re-freezing
```

### Catalog Server (REST API)

```python
# Start the catalog REST API — exposes all catalog operations over HTTP
import uvicorn
from permafrost import PermafrostCatalogServer

srv = PermafrostCatalogServer("catalog.db")
uvicorn.run(srv.app, host="0.0.0.0", port=8800)
# Interactive docs available at http://localhost:8800/docs
```

```bash
# Or via CLI
permafrost catalog serve --db catalog.db --host 0.0.0.0 --port 8800
```

```bash
# Then call it from anywhere — no Python SDK needed
curl http://localhost:8800/health
curl http://localhost:8800/datasets
curl http://localhost:8800/cost_report?tier=glacier_deep
curl -X POST http://localhost:8800/datasets/register \
     -H "Content-Type: application/json" \
     -d '{"path": "/data/sales.permafrost", "name": "sales", "version": "v2024"}'
```

### Distributed Cluster

```python
from permafrost import PermafrostClient

client = PermafrostClient("http://master:8700")
job_id = client.freeze("large_data.csv", "s3://bucket/output.permafrost")
status = client.wait(job_id)
print(status)  # {"status": "done", "ratio": 10.2, "workers_used": 4}
```

### Apache Spark

```python
from permafrost.spark import register

register(spark)
df = spark.read.format("permafrost").load("s3://bucket/data.permafrost")
df.filter(df.year == 2023).show()   # pushdown via sparse index — skips irrelevant chunks
```

### CLI

```bash
# Compress
permafrost freeze sales.csv sales.permafrost --codec lzma2 --partition-by year

# Decompress with filter
permafrost unfreeze sales.permafrost --filter '{"year": 2023}' --output sales_2023.csv

# Audit (without decompressing)
permafrost audit sales.permafrost

# Verify integrity of all chunks
permafrost verify sales.permafrost

# Catalog
permafrost catalog register s3://bucket/cold/
permafrost catalog search --name sales
permafrost catalog cost-report --tier glacier_deep
```

---

## Benchmarks

**Dataset:** 210,000,000 rows × 13 columns — real corporate data (IDs, timestamps, categories, floats, integers)  
**Period:** 2020–2024, partitioned by year | **Measured locally — not estimates.**

### Compression vs. alternatives

| Format | Size | Ratio | Write time |
|--------|------|-------|------------|
| Raw CSV | 16.35 GB | 1.00× | — |
| Parquet + Snappy | 5.89 GB | 2.78× | 8.9 min |
| CSV + plain LZMA2 *(p9)* | ~3.80 GB | ~4.3× | **~7 h** ⚠️ |
| **Permafrost + ZSTD** | **3.25 GB** | **5.03×** | **77.7 min** |
| **Permafrost + LZMA2** | **3.03 GB** | **5.40×** | **93.5 min** |

> ⚠️ Plain LZMA2 at preset=9 on 16 GB would take ~7 hours — **impractical for real data pipelines**. Permafrost delivers a higher ratio because column predictors reduce entropy before the codec: the same LZMA2 receives a highly structured stream, works less, and finishes faster.

### Selective Read — Sparse Index

```python
# Read only 2022 from a file with 5 years / 210M rows of data
df_2022 = pf.unfreeze("history.permafrost", filter={"year": 2022})
# 42,007,186 rows in 5.7 min — read only 20% of the file, 80% never touched
```

With CSV, Parquet or `.xz`, you would need to decompress the full 16.35 GB to access a single year.

### Audit — metadata without decompressing

```python
info = pf.audit("history.permafrost")   # 9.2s for 210M rows — no decompression
# {"orig_rows": 210000000, "n_chunks": 4200, "codec": "lzma2", ...}
```

### Why does Permafrost compress better than plain LZMA2?

**Column predictors** transform data *before* the codec — each column type becomes a simpler, more regular stream:

| Predictor | Column type | Transformation |
|-----------|-------------|----------------|
| `delta_zigzag` | IDs, integers | difference between consecutive values → small values near zero |
| `lag1_zigzag` | floats, prices | lag-1 residual × scale → small values near zero |
| `ts_delta_s` | timestamps | delta in seconds → small integers |
| `category_u8` | categories (≤256 values) | string → uint8 index (1 byte per row) |
| `json_schema_v2` | JSON columns | key compression via shared dictionary |

Plain LZMA2 receives 16 GB of semi-random bytes and takes ~7 hours. Permafrost delivers the same LZMA2 a highly structured stream — 26% better ratio in 93 minutes.

### Cloud storage cost (S3 Glacier Deep Archive)

| Original volume | Without Permafrost | With Permafrost (5.4×) | Monthly savings |
|-----------------|--------------------|------------------------|----------------|
| 1 TB | $0.99 | **$0.18** | **-81%** |
| 10 TB | $9.90 | **$1.83** | **-81%** |
| 100 TB | $99.00 | **$18.33** | **-81%** |

---

## `.permafrost` Format v1.3

The format is self-describing — readable without external documentation:

```
[MAGIC: "PRMS" 4B]              identification
[VERSION: 1.3 2B]
[FLAGS: bitmask 2B]             delta | quantize | chunked | predictor | index
[CODEC_ID: 1B]                  0x01=Zstd | 0x02=LZMA2 | 0x03=ZPAQ
[QUANT: 1B]                     0x00=lossless | 0x01=high | 0x02=medium | 0x03=low
[N_CHUNKS: 2B]
[SCHEMA ARROW: var]             full schema embedded
[PREDICTOR MANIFEST: JSON]      predictor and metadata per column
[COMMENT: var]
[FREEZE_TIMESTAMP: int64]
[ORIGINAL_ROWS: uint64]
[HEADER SHA-256: 32B]           header integrity
[CHUNK_0: u32_len + data + sha256] × N
[SPARSE INDEX: JSON]            byte_offset of each chunk
[INDEX_SHA256: 32B]
[EOF: "SMRP" 4B]                PRMS reversed
```

---

## API Reference

### Core

| Function | Description |
|----------|-------------|
| `pf.freeze(df, path, ...)` | Compress a DataFrame; accepts `codec_profile` and `per_column_codec` |
| `pf.unfreeze(path, filter=None, verify=False)` | Decompress; `filter` uses sparse index |
| `pf.append(df, path)` | Append rows to an existing file without re-freezing |
| `pf.audit(path)` | Returns metadata without decompressing |
| `pf.diff(path_a, path_b)` | Row-level diff between two `.permafrost` files |

### Query engine

| Function | Description |
|----------|-------------|
| `pf.register(alias, path)` | Register a file (local or `s3://`) under a SQL alias |
| `pf.unregister(alias)` | Remove a registered alias |
| `pf.registered()` | Returns all active alias → path mappings |
| `pf.query(sql)` | Run a SQL query over registered files via DuckDB |
| `pf.set_query_backend(backend)` | Set a remote backend for resolving `s3://` aliases |

### Streaming

| Function | Description |
|----------|-------------|
| `pf.freeze_file(csv_path, out_path, chunk_rows=50_000)` | Compress large CSV without loading into memory |
| `pf.freeze_stream(cursor_gen, out_path)` | Compress from a generator |
| `pf.peek(path, batch_size=50_000)` | Decompress in iterative batches |

### Cloud

| Function | Description |
|----------|-------------|
| `pf.freeze_to(df, uri)` | Compress and upload directly to S3/GCS/Azure |
| `pf.thaw_from(uri, filter=None)` | Decompress from cloud with Range Request |
| `pf.audit_remote(uri)` | Audit remote file without downloading everything |
| `pf.storage_from_uri(uri)` | Returns the appropriate storage adapter for the URI |

### Catalog Server

| Class/Endpoint | Description |
|----------------|-------------|
| `PermafrostCatalogServer(db_path)` | Create a FastAPI server wrapping a PermafrostCatalog |
| `GET /health` | Server status and dataset count |
| `POST /datasets/register` | Register a file (body: `path`, `name`, `version`, `tags`) |
| `POST /datasets/register_dir` | Register all `.permafrost` files in a directory |
| `GET /datasets` | List/search datasets (query params mirror `catalog.search()`) |
| `GET /datasets/{name}/versions` | All registered versions of a dataset |
| `GET /datasets/{name}/chunks` | Sparse index entries for a dataset |
| `GET /datasets/{name}/integrity` | SHA-256 integrity check for a dataset |
| `DELETE /datasets/{name}` | Remove dataset from catalog (file not deleted) |
| `GET /stats` | Aggregate metrics (total datasets, rows, MB) |
| `GET /cost_report` | Cost estimate by storage tier |
| `POST /sql` | Execute SQL directly against the catalog DuckDB |
| `GET /search` | Search alias with query params |

### Catalog

| Class/Method | Description |
|--------------|-------------|
| `PermafrostCatalog(db_path, backend=None)` | Create or open a DuckDB catalog; optional remote backend |
| `.configure(backend)` | Replace the active storage backend at runtime |
| `.register(path, name=None, version=None)` | Register a file; `version=` enables versioned datasets |
| `.register_dir(path_or_uri)` | Index all `.permafrost` files in a directory |
| `.versions(name)` | Returns a DataFrame with all registered versions of a dataset |
| `.unfreeze(name, version=None)` | Decompress; `version=` selects a specific registered version |
| `.search(name, lossless_only, codec, tags_contain)` | Search by metadata |
| `.cost_report(tier)` | Estimate monthly cost by storage tier |
| `.integrity_check()` | Verify SHA-256 of all indexed files |
| `LocalCatalogBackend()` | Default backend — resolves local paths |
| `S3CatalogBackend(bucket)` | Downloads from S3 with ETag cache + LRU eviction |
| `GCSCatalogBackend(bucket)` | Downloads from Google Cloud Storage |
| `AzureCatalogBackend(container, connection_string)` | Downloads from Azure Blob Storage |

### Cluster

| Class/Method | Description |
|--------------|-------------|
| `PermafrostMaster(host, port)` | Start the cluster master node |
| `PermafrostWorker(master_url)` | Start a worker that registers with the master |
| `PermafrostClient(master_url)` | Client to submit jobs to the cluster |
| `client.freeze(input, output)` | Submit a freeze job to the cluster |
| `client.wait(job_id)` | Wait for job completion |

### Codecs and profiles

| Constant / value | Description |
|------------------|-------------|
| `pf.CODEC_ZSTD` | Zstandard — fast, good ratio |
| `pf.CODEC_LZMA2` | LZMA2 — higher ratio, slower |
| `pf.CODEC_ZPAQ` | ZPAQ — maximum ratio, very slow |
| `codec_profile="balanced"` | ZSTD for all columns — default |
| `codec_profile="max_compression"` | LZMA2 for all columns |
| `codec_profile="max_speed"` | ZSTD L1 for all columns |
| `codec_profile="auto"` | Selects codec per column based on data profile |
| `per_column_codec={"col": "lzma2"}` | Override codec on a per-column basis |

---

## Tests

The test suite covers 1261+ scenarios including edge cases, minimum benchmarks, full fidelity and fault tolerance:

```
test_freeze_thaw.py              freeze/thaw/audit/integrity/sparse index
test_sparse_index.py             chunked freeze, selective thaw, bit-rot detection
test_catalog.py                  register, search, thaw, cost, integrity, SQL
test_catalog_backends.py         LocalBackend, versioning, per-dataset version selection
test_catalog_backends_cloud.py   S3/GCS/Azure with mocked clients, ETag cache, LRU
test_cluster.py                  health, lifecycle, concurrency, cancellation
test_cluster_fault_tolerance.py  retry, no workers, 10 parallel jobs
test_comprehensive.py            edge cases, all codecs, minimum benchmarks
test_fidelidade_total.py         100% row-by-row, distributions, multi round-trip
test_concorrencia.py             10 simultaneous threads, parallel freeze+thaw
test_predictor_edge_cases.py     zero variance, 256 categories, extreme timestamps
test_formato_binario_spec.py     byte-by-byte format, SHA-256, sparse index
test_schema_detector_stress.py   50% missing fields, mixed types, 100 fields
test_cli_cobertura.py            all CLI commands
test_performance_regression.py   ratio ≥8×, thaw <2s, audit <50ms
test_per_column_codec.py         per_column_codec, codec_profile, mixed codecs
test_query.py                    register/query/unregister, SQL joins, remote aliases
```

Current test status: [![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github&cacheSeconds=1)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)

---

## Docker — Cluster in production

```bash
# Start cluster with 4 workers
docker-compose up --scale worker=4

# Local build
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up --scale worker=2
```

Images available on Docker Hub:
- `caua-ferreira/permafrost-master`
- `caua-ferreira/permafrost-worker`

---

## Contributing

Contributions are welcome! See the [contributing guide](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md).

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
pip install -e ".[dev]"
pytest tests/ -v
```

---

## License

Apache License 2.0 — see [LICENSE](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE).

---

<div align="center">

Made with ❄️ for data that needs to last decades.

</div>

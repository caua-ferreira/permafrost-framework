# ❄️ Permafrost Framework

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/permafrost-framework/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/permafrost-framework?color=blue)](https://pypi.org/project/permafrost-framework/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)
[![Coverage](https://img.shields.io/codecov/c/github/caua-ferreira/permafrost-framework?logo=codecov)](https://codecov.io/gh/caua-ferreira/permafrost-framework)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/caua-ferreira/permafrost-framework/badge)](https://securityscorecards.dev/viewer/?uri=github.com/caua-ferreira/permafrost-framework)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs-00D4FF)](https://caua-ferreira.github.io/permafrost-framework)


**Distributed intelligent compression platform for long-term digital archiving.**

*210 million rows: Permafrost + LZMA2 = 3.03 GB vs CSV = 16.35 GB (5.4×) — nearly 2× better than Parquet. Query a single year from 5 years of data: 42M rows read, only 20% of the file touched.*

[Documentation](https://caua-ferreira.github.io/permafrost-framework) · [Quick Start](#quick-start) · [Benchmarks](#benchmarks) · [API](#api-reference) · [Contributing](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md)

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
- **Selective reads** — embedded sparse index enables `filter={"year": 2023}` without decompressing the rest
- **Guaranteed integrity** — SHA-256 per chunk, verified before any decompression
- **Self-describing** — full Arrow schema embedded in the file; readable in 2040 without external documentation
- **Cloud-native** — native support for S3, Google Cloud Storage and Azure Blob Storage with HTTP Range Requests
- **DuckDB Catalog** — metadata search across hundreds of remote files without downloading any of them
- **Streaming** — process datasets larger than RAM with `freeze_file()` and `thaw_iter()`
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
df_back = pf.thaw("sales.permafrost", verify=True)

# Decompress only 2023 — reads only the chunks for that year
df_2023 = pf.thaw("sales.permafrost", filter={"year": 2023})
```

### Streaming (datasets larger than RAM)

```python
# Freeze a large file without loading it into memory
pf.freeze_file("100gb.csv", "output.permafrost", chunk_rows=50_000)

# Iterative thaw in batches
for batch_df in pf.thaw_iter("output.permafrost", batch_size=50_000):
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

### Catalog — search across multiple files

```python
cat = pf.PermafrostCatalog("catalog.db")
cat.register_dir("s3://my-bucket/cold/")   # indexes metadata without downloading

# Search by name, codec, lossless
results = cat.search(name="sales", lossless_only=True)

# Estimated cost report for Glacier Deep Archive
cat.cost_report("glacier_deep")

# Bulk integrity check
cat.integrity_check()
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
permafrost thaw sales.permafrost --filter '{"year": 2023}' --output sales_2023.csv

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
df_2022 = pf.thaw("history.permafrost", filter={"year": 2022})
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
| `pf.freeze(df, path, ...)` | Compress a DataFrame to a `.permafrost` file |
| `pf.thaw(path, filter=None, verify=False)` | Decompress; `filter` uses sparse index |
| `pf.audit(path)` | Returns metadata without decompressing |

### Streaming

| Function | Description |
|----------|-------------|
| `pf.freeze_file(csv_path, out_path, chunk_rows=50_000)` | Compress large CSV without loading into memory |
| `pf.freeze_stream(cursor_gen, out_path)` | Compress from a generator |
| `pf.thaw_iter(path, batch_size=50_000)` | Decompress in iterative batches |

### Cloud

| Function | Description |
|----------|-------------|
| `pf.freeze_to(df, uri)` | Compress and upload directly to S3/GCS/Azure |
| `pf.thaw_from(uri, filter=None)` | Decompress from cloud with Range Request |
| `pf.audit_remote(uri)` | Audit remote file without downloading everything |
| `pf.storage_from_uri(uri)` | Returns the appropriate storage adapter for the URI |

### Catalog

| Class/Method | Description |
|--------------|-------------|
| `PermafrostCatalog(db_path)` | Create or open a DuckDB catalog |
| `.register_dir(path_or_uri)` | Index all `.permafrost` files in a directory |
| `.search(name, lossless_only, codec)` | Search by metadata |
| `.cost_report(tier)` | Estimate monthly cost by storage tier |
| `.integrity_check()` | Verify SHA-256 of all indexed files |

### Cluster

| Class/Method | Description |
|--------------|-------------|
| `PermafrostMaster(host, port)` | Start the cluster master node |
| `PermafrostWorker(master_url)` | Start a worker that registers with the master |
| `PermafrostClient(master_url)` | Client to submit jobs to the cluster |
| `client.freeze(input, output)` | Submit a freeze job to the cluster |
| `client.wait(job_id)` | Wait for job completion |

### Available codecs

| Constant | Description |
|----------|-------------|
| `pf.CODEC_ZSTD` | Zstandard — fast, good ratio |
| `pf.CODEC_LZMA2` | LZMA2 — higher ratio, slower |
| `pf.CODEC_ZPAQ` | ZPAQ — maximum ratio, very slow |

---

## Tests

The test suite covers 268+ scenarios including edge cases, minimum benchmarks, full fidelity and fault tolerance:

```
test_freeze_thaw.py              freeze/thaw/audit/integrity/sparse index
test_sparse_index.py             chunked freeze, selective thaw, bit-rot detection
test_catalog.py                  register, search, thaw, cost, integrity, SQL
test_cluster.py                  health, lifecycle, concurrency, cancellation
test_comprehensive.py            edge cases, all codecs, minimum benchmarks
test_fidelidade_total.py         100% row-by-row, distributions, multi round-trip
test_concorrencia.py             10 simultaneous threads, parallel freeze+thaw
test_predictor_edge_cases.py     zero variance, 256 categories, extreme timestamps
test_cluster_fault_tolerance.py  retry, no workers, 10 parallel jobs
test_formato_binario_spec.py     byte-by-byte format, SHA-256, sparse index
test_schema_detector_stress.py   50% missing fields, mixed types, 100 fields
test_cli_cobertura.py            all CLI commands
test_performance_regression.py   ratio ≥8×, thaw <2s, audit <50ms
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

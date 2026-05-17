# ❄️ Permafrost Framework

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/permafrost-framework/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/permafrost-framework?color=blue)](https://pypi.org/project/permafrost-framework/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)

**面向长期数字归档的智能压缩平台。**

*2.1亿行数据：Permafrost + LZMA2 = 3.03 GB vs CSV = 16.35 GB（5.4×）——比 Parquet 好近 2×。从5年数据中查询单年：读取4200万行，仅触及20%的文件。*

🌐 [English](README.md) · [Português (BR)](README.pt-BR.md) · [Español](README.es.md) · [Français](README.fr.md) · **中文** · [العربية](README.ar.md) · [हिन्दी](README.hi.md)

[文档](https://caua-ferreira.github.io/permafrost-framework) · [快速开始](#快速开始) · [性能基准](#性能基准) · [API参考](#api-参考)

</div>

---

## 什么是 Permafrost？

企业历史数据——CSV、JSONL、数据库转储——以高昂的成本存储在冷存储（S3 Glacier、Azure Archive）中多年。问题在于：如果你需要10 GB文件中某一个月的数据，你必须解压**全部**内容。

Permafrost 通过两个机制解决这一问题：

1. **列预测器** — 在压缩前对数据进行语义转换（delta、zigzag、时间戳、类别），实现远超纯 LZMA2 的压缩比
2. **稀疏索引（Sparse Index）** — 嵌入文件中的索引，精确指向每个数据块的字节偏移量，通过 HTTP Range Request 实现选择性读取，无需下载整个文件

```
2.1亿行 × 13列 — 本地实测基准数据：

原始 CSV：              16.35 GB  (1.00×)
Parquet + Snappy：       5.89 GB  (2.78×)   写入：  8.9 分钟
CSV + 纯 LZMA2 (p9)：  ~3.80 GB  (~4.3×)   写入：  ~7 小时  ⚠️ 不切实际
Permafrost + ZSTD：      3.25 GB  (5.03×)   写入： 77.7 分钟
Permafrost + LZMA2：     3.03 GB  (5.40×)   写入： 93.5 分钟   ← 比 Parquet 好近 2×

仅查询2022年 → 5.7分钟内获得4200万行 — 仅读取20%的文件，80%从未被访问
```

---

## 功能特性

- **高压缩比** — 使用列预测器（delta_zigzag、lag1_zigzag、ts_delta_s、category_u8）进行 Zstd / LZMA2 / ZPAQ 压缩前的数据预处理
- **选择性读取** — 嵌入式稀疏索引支持 `filter={"year": 2023}`，无需解压其余数据
- **完整性保障** — 每个数据块均有 SHA-256 校验，在任何解压前进行验证
- **自描述格式** — 完整的 Arrow Schema 嵌入文件中；2040年无需外部文档即可读取
- **云原生** — 原生支持 S3、Google Cloud Storage 和 Azure Blob Storage，支持 HTTP Range Request
- **DuckDB 目录** — 无需下载，即可搜索数百个远程文件的元数据
- **流式处理** — 使用 `freeze_file()` 和 `peek()` 处理超过内存大小的数据集
- **分布式集群** — Master + Workers 通过 FastAPI；N 个 worker 并行处理 1 TB
- **加密** — AES-256-GCM 逐块加密，存储开销 0.00%
- **完整CLI** — `permafrost freeze / unfreeze / audit / verify / catalog`

---

## 安装

```bash
# 基础安装
pip install permafrost-framework

# 支持 AWS S3
pip install "permafrost-framework[s3]"

# 支持 Google Cloud Storage
pip install "permafrost-framework[gcs]"

# 支持 Azure Blob Storage
pip install "permafrost-framework[azure]"

# 所有云提供商
pip install "permafrost-framework[all-cloud]"
```

**要求：** Python 3.10+

---

## 快速开始

### 基础 Freeze 和 Unfreeze

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("sales_history.csv")

# 压缩 — 返回指标
metrics = pf.freeze(df, "sales.permafrost", codec=pf.CODEC_LZMA2, partition_by="year")
print(f"压缩比: {metrics['ratio']:.2f}×  |  {metrics['original_mb']:.1f} MB → {metrics['stored_mb']:.1f} MB")

# 解压全部
df_back = pf.unfreeze("sales.permafrost", verify=True)

# 仅解压2023年 — 只读取该年的数据块
df_2023 = pf.unfreeze("sales.permafrost", filter={"year": 2023})
```

### 流式处理（超过内存大小的数据集）

```python
# 无需加载到内存即可压缩大文件
pf.freeze_file("100gb.csv", "output.permafrost", chunk_rows=50_000)

# 分批迭代读取
for batch_df in pf.peek("output.permafrost", batch_size=50_000):
    process(batch_df)
```

### 云存储（S3、GCS、Azure）

```python
# 直接上传到 S3
pf.freeze_to(df, "s3://my-bucket/data/sales.permafrost")

# 从 S3 选择性读取 — 不下载整个文件
df_2023 = pf.thaw_from("s3://my-bucket/data/sales.permafrost", filter={"year": 2023})

# 远程审计，无需下载任何内容
info = pf.audit_remote("s3://my-bucket/data/sales.permafrost")
```

### 命令行（CLI）

```bash
# 压缩
permafrost freeze sales.csv sales.permafrost --codec lzma2 --partition-by year

# 带过滤条件解压
permafrost unfreeze sales.permafrost --filter '{"year": 2023}' --output sales_2023.csv

# 审计（无需解压）
permafrost audit sales.permafrost
```

---

## 性能基准

### 压缩对比

| 格式 | 大小 | 压缩比 | 写入时间 |
|------|------|--------|---------|
| 原始 CSV | 16.35 GB | 1.00× | — |
| Parquet + Snappy | 5.89 GB | 2.78× | 8.9 分钟 |
| CSV + 纯 LZMA2 *(p9)* | ~3.80 GB | ~4.3× | **~7 小时** ⚠️ |
| **Permafrost + ZSTD** | **3.25 GB** | **5.03×** | **77.7 分钟** |
| **Permafrost + LZMA2** | **3.03 GB** | **5.40×** | **93.5 分钟** |

### 云存储成本（S3 Glacier Deep Archive）

| 原始数据量 | 不使用 Permafrost | 使用 Permafrost（5.4×）| 每月节省 |
|-----------|-------------------|----------------------|---------|
| 1 TB | $0.99 | **$0.18** | **-81%** |
| 10 TB | $9.90 | **$1.83** | **-81%** |
| 100 TB | $99.00 | **$18.33** | **-81%** |

---

## API 参考

| 函数 | 描述 |
|------|------|
| `pf.freeze(df, path, ...)` | 将 DataFrame 压缩为 `.permafrost` 文件 |
| `pf.unfreeze(path, filter=None)` | 解压；`filter` 使用稀疏索引 |
| `pf.audit(path)` | 返回元数据，无需解压 |
| `pf.freeze_append(path, df_new)` | 向现有文件追加行，无需重新压缩 |
| `pf.peek(path, batch_size=50_000)` | 分批迭代解压 |
| `pf.freeze_to(df, uri)` | 压缩并直接上传到云存储 |
| `pf.thaw_from(uri, filter=None)` | 使用 Range Request 从云端解压 |

---

## 贡献

欢迎贡献！请查看[贡献指南](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md)。

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
pip install -e ".[dev]"
pytest tests/ -v
```

---

## 许可证

Apache License 2.0 — 查看 [LICENSE](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)。

---

<div align="center">

用 ❄️ 打造，为需要保存数十年的数据而生。

</div>

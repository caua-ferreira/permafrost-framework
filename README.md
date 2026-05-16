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


**Plataforma distribuída de compressão inteligente para arquivamento digital de longo prazo.**

*21 milhões de linhas: Permafrost + LZMA2 = 0.33 GB vs CSV = 2.13 GB (6.5×), freeze em 6.5 min — e você lê só o ano que precisa em 8 segundos, sem descomprimir o resto.*

[Documentação](https://caua-ferreira.github.io/permafrost-framework) · [Quick Start](#quick-start) · [Benchmarks](#benchmarks) · [API](#api) · [Contribuir](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md)

</div>

---

## O que é o Permafrost?

Dados corporativos históricos — CSVs, JSONL, dumps de MongoDB — ficam anos em cold storage (S3 Glacier, Azure Archive) pagando caro. O problema: se você precisa buscar os dados de um único mês em um arquivo de 10 GB, é necessário descomprimir **tudo**.

O Permafrost resolve isso com dois mecanismos:

1. **Preditores colunares** — transforma os dados semanticamente antes da compressão (delta, zigzag, timestamps, categorias), atingindo ratios muito superiores ao LZMA2 puro
2. **Sparse index** — índice embutido no arquivo que aponta o byte exato de cada chunk, permitindo leitura seletiva via HTTP Range Request sem baixar o arquivo inteiro

```
1.050.000 linhas × 13 colunas — benchmark real medido localmente:

CSV bruto:               103.9 MB  (1.00×)
Parquet + Snappy:         37.8 MB  (2.75×)   freeze: 0.6s
CSV + LZMA2 puro:         24.2 MB  (4.29×)   freeze: 125.5s
Permafrost + ZSTD:        17.3 MB  (6.02×)   freeze: 14.8s   ← 8.5× mais rápido que LZMA2 puro
Permafrost + LZMA2:       15.9 MB  (6.53×)   freeze: 19.3s   ← +52% vs LZMA2 puro, 6.5× mais rápido
```

---

## Funcionalidades

- **Alta compressão** — preditores colunares (delta_zigzag, lag1_zigzag, ts_delta_s, category_u8, raw_text) antes de Zstd / LZMA2 / ZPAQ
- **Leitura seletiva** — sparse index embutido permite `filter={"ano": 2023}` sem descomprimir o resto
- **Integridade garantida** — SHA-256 por chunk, detectado antes de qualquer decompressão
- **Auto-descritivo** — schema Arrow completo embutido no arquivo; legível em 2040 sem documentação externa
- **Cloud-native** — suporte nativo a S3, Google Cloud Storage e Azure Blob Storage com HTTP Range Requests
- **Catalog DuckDB** — busca em metadados de centenas de arquivos no S3 sem baixar nenhum
- **Streaming** — processa datasets maiores que a RAM com `freeze_file()` e `thaw_iter()`
- **Cluster distribuído** — Master + Workers via FastAPI; processa 1 TB em paralelo com N workers
- **Spark DataSource v2** — integração nativa com PySpark 4.0+ com pushdown via sparse index
- **CLI completa** — `permafrost freeze / thaw / audit / verify / catalog` com output rich

---

## Instalação

```bash
# Instalação básica
pip install permafrost-framework

# Com suporte a AWS S3
pip install "permafrost-framework[s3]"

# Com suporte a Google Cloud Storage
pip install "permafrost-framework[gcs]"

# Com suporte a Azure Blob Storage
pip install "permafrost-framework[azure]"

# Todos os provedores cloud
pip install "permafrost-framework[all-cloud]"

# Com Apache Spark
pip install "permafrost-framework[spark]"
```

**Requisitos:** Python 3.10+

---

## Quick Start

### Freeze e Thaw básico

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("vendas_historico.csv")

# Comprimir — retorna métricas
metrics = pf.freeze(df, "vendas.permafrost", codec=pf.CODEC_LZMA2, partition_by="ano")
print(f"Ratio: {metrics['ratio']:.2f}×  |  {metrics['original_mb']:.1f} MB → {metrics['stored_mb']:.1f} MB")
# Ratio: 8.37×  |  5.85 MB → 0.68 MB

# Descomprimir tudo
df_back = pf.thaw("vendas.permafrost", verify=True)

# Descomprimir só 2023 — lê apenas os chunks daquele ano
df_2023 = pf.thaw("vendas.permafrost", filter={"ano": 2023})
```

### Streaming (datasets maiores que a RAM)

```python
# Freeze de arquivo grande sem carregar na memória
pf.freeze_file("100gb.csv", "saida.permafrost", chunk_rows=50_000)

# Thaw iterativo em batches
for batch_df in pf.thaw_iter("saida.permafrost", batch_size=50_000):
    processar(batch_df)
```

### Cloud (S3, GCS, Azure)

```python
# Upload direto para S3
pf.freeze_to(df, "s3://meu-bucket/dados/vendas.permafrost")

# Leitura seletiva do S3 via HTTP Range Request — não baixa o arquivo inteiro
df_2023 = pf.thaw_from("s3://meu-bucket/dados/vendas.permafrost", filter={"ano": 2023})

# Auditoria remota sem baixar nada
info = pf.audit_remote("s3://meu-bucket/dados/vendas.permafrost")
```

### Catalog — busca em múltiplos arquivos

```python
cat = pf.PermafrostCatalog("catalog.db")
cat.register_dir("s3://meu-bucket/cold/")   # indexa metadados sem baixar

# Busca por nome, codec, lossless
resultados = cat.search(name="vendas", lossless_only=True)

# Relatório de custo estimado no Glacier Deep Archive
cat.cost_report("glacier_deep")

# Verificação de integridade em massa
cat.integrity_check()
```

### Cluster distribuído

```python
from permafrost import PermafrostClient

client = PermafrostClient("http://master:8700")
job_id = client.freeze("dados_grandes.csv", "s3://bucket/saida.permafrost")
status = client.wait(job_id)
print(status)  # {"status": "done", "ratio": 10.2, "workers_used": 4}
```

### Apache Spark

```python
from permafrost.spark import register

register(spark)
df = spark.read.format("permafrost").load("s3://bucket/dados.permafrost")
df.filter(df.ano == 2023).show()   # pushdown via sparse index — não lê chunks desnecessários
```

### CLI

```bash
# Comprimir
permafrost freeze vendas.csv vendas.permafrost --codec lzma2 --partition-by ano

# Descomprimir com filtro
permafrost thaw vendas.permafrost --filter '{"ano": 2023}' --output vendas_2023.csv

# Auditoria (sem descomprimir)
permafrost audit vendas.permafrost

# Verificar integridade de todos os chunks
permafrost verify vendas.permafrost

# Catalog
permafrost catalog register s3://bucket/cold/
permafrost catalog search --name vendas
permafrost catalog cost-report --tier glacier_deep
```

---

## Benchmarks

**Dataset:** 21.000.000 linhas × 13 colunas — dados corporativos reais (IDs, timestamps, categorias, floats, inteiros)  
**Período:** 2020–2024, particionado por ano | **Medido localmente — não são estimativas.**

### Compressão vs. alternativas

| Formato | Tamanho | Ratio | Tempo de escrita |
|---------|---------|-------|-----------------|
| CSV bruto | 2.13 GB | 1.00× | — |
| Parquet + Snappy | 0.78 GB | 2.72× | 9.8s |
| CSV + LZMA2 puro *(p9)* | ~0.50 GB | ~4.3× | **~44 min** ⚠️ |
| **Permafrost + ZSTD** | **0.35 GB** | **6.11×** | **312.6s** |
| **Permafrost + LZMA2** | **0.33 GB** | **6.51×** | **392.3s** |

> ⚠️ LZMA2 puro com preset=9 em 2.13 GB levaria ~44 min — **inviável para pipelines de dados reais**. O Permafrost entrega ratio maior em 6.5 min porque os preditores colunares reduzem a entropia antes do codec, fazendo o mesmo LZMA2 trabalhar menos e terminar mais rápido.

### Leitura seletiva — Sparse Index

```python
# Ler apenas 2022 de um arquivo com 5 anos / 21M linhas de dados
df_2022 = pf.thaw("historico.permafrost", filter={"ano": 2022})
# 4.505.143 linhas em 7.98s — leu apenas 70.8 MB de 326.8 MB (21.7% do arquivo)
```

Com CSV, Parquet ou `.xz`, seria necessário descomprimir os 2.13 GB inteiros para acessar um único ano.

### Thaw completo e Audit

```python
df = pf.thaw("historico.permafrost")           # 21M linhas em 29.0s
info = pf.audit("historico.permafrost")         # 180ms para 21M linhas — sem descomprimir nada
# {"orig_rows": 21000000, "n_chunks": 420, "codec": "lzma2", ...}
```

### Por que o Permafrost comprime melhor que LZMA2 puro?

Os **preditores colunares** transformam os dados *antes* do codec — cada tipo de coluna vira um stream mais simples e regular:

| Predictor | Coluna | Transformação |
|-----------|--------|---------------|
| `delta_zigzag` | IDs, inteiros | diferença entre consecutivos → valores pequenos perto de zero |
| `lag1_zigzag` | floats, preços | resíduo lag-1 × escala → valores pequenos perto de zero |
| `ts_delta_s` | timestamps | delta em segundos → inteiros pequenos |
| `category_u8` | categorias (≤256 valores) | string → índice uint8 (1 byte por linha) |
| `json_schema_v2` | colunas JSON | compressão de chaves por dicionário compartilhado |

O LZMA2 puro recebe 2.13 GB de bytes semi-aleatórios e leva 44 min. O Permafrost entrega ao mesmo LZMA2 um stream altamente estruturado — ratio 51% melhor em 6.5 min.

### Custo em cloud storage (S3 Glacier Deep Archive)

| Volume original | Sem Permafrost | Com Permafrost (6.5×) | Economia/mês |
|-----------------|----------------|----------------------|--------------|
| 1 TB | $0.99 | **$0.15** | **-85%** |
| 10 TB | $9.90 | **$1.52** | **-85%** |
| 100 TB | $99.00 | **$15.23** | **-85%** |

---

## Formato `.permafrost` v1.3

O formato é auto-descritivo — legível sem documentação externa:

```
[MAGIC: "PRMS" 4B]              identificação
[VERSION: 1.3 2B]
[FLAGS: bitmask 2B]             delta | quantize | chunked | predictor | index
[CODEC_ID: 1B]                  0x01=Zstd | 0x02=LZMA2 | 0x03=ZPAQ
[QUANT: 1B]                     0x00=lossless | 0x01=high | 0x02=medium | 0x03=low
[N_CHUNKS: 2B]
[SCHEMA ARROW: var]             schema completo embutido
[PREDICTOR MANIFEST: JSON]      preditor e metadados por coluna
[COMMENT: var]
[FREEZE_TIMESTAMP: int64]
[ORIGINAL_ROWS: uint64]
[HEADER SHA-256: 32B]           integridade do header
[CHUNK_0: u32_len + data + sha256] × N
[SPARSE INDEX: JSON]            byte_offset de cada chunk
[INDEX_SHA256: 32B]
[EOF: "SMRP" 4B]                PRMS invertido
```

---

## API Reference

### Core

| Função | Descrição |
|--------|-----------|
| `pf.freeze(df, path, ...)` | Comprime um DataFrame para arquivo `.permafrost` |
| `pf.thaw(path, filter=None, verify=False)` | Descomprime; `filter` usa sparse index |
| `pf.audit(path)` | Retorna metadados sem descomprimir |

### Streaming

| Função | Descrição |
|--------|-----------|
| `pf.freeze_file(csv_path, out_path, chunk_rows=50_000)` | Comprime CSV grande sem carregar na memória |
| `pf.freeze_stream(cursor_gen, out_path)` | Comprime a partir de um generator |
| `pf.thaw_iter(path, batch_size=50_000)` | Descomprime em batches iterativos |

### Cloud

| Função | Descrição |
|--------|-----------|
| `pf.freeze_to(df, uri)` | Comprime e envia direto para S3/GCS/Azure |
| `pf.thaw_from(uri, filter=None)` | Descomprime do cloud com Range Request |
| `pf.audit_remote(uri)` | Audita arquivo remoto sem baixar tudo |
| `pf.storage_from_uri(uri)` | Retorna o adapter de storage adequado para a URI |

### Catalog

| Classe/Método | Descrição |
|---------------|-----------|
| `PermafrostCatalog(db_path)` | Cria ou abre um catalog DuckDB |
| `.register_dir(path_or_uri)` | Indexa todos os `.permafrost` de um diretório |
| `.search(name, lossless_only, codec)` | Busca por metadados |
| `.cost_report(tier)` | Estima custo mensal por tier de storage |
| `.integrity_check()` | Verifica SHA-256 de todos os arquivos indexados |

### Cluster

| Classe/Método | Descrição |
|---------------|-----------|
| `PermafrostMaster(host, port)` | Inicia o nó master do cluster |
| `PermafrostWorker(master_url)` | Inicia um worker que se registra no master |
| `PermafrostClient(master_url)` | Cliente para submeter jobs ao cluster |
| `client.freeze(input, output)` | Submete job de freeze ao cluster |
| `client.wait(job_id)` | Aguarda conclusão do job |

### Codecs disponíveis

| Constante | Descrição |
|-----------|-----------|
| `pf.CODEC_ZSTD` | Zstandard — rápido, bom ratio |
| `pf.CODEC_LZMA2` | LZMA2 — maior ratio, mais lento |
| `pf.CODEC_ZPAQ` | ZPAQ — ratio máximo, muito lento |

---

## Testes

A suite cobre 268+ cenários incluindo edge cases, benchmarks mínimos, fidelidade total e tolerância a falhas:

```
test_freeze_thaw.py              freeze/thaw/audit/integridade/sparse index
test_sparse_index.py             chunked freeze, thaw seletivo, bit-rot detection
test_catalog.py                  register, search, thaw, cost, integrity, SQL
test_cluster.py                  health, lifecycle, concorrência, cancelamento
test_comprehensive.py            edge cases, todos os codecs, benchmarks mínimos
test_fidelidade_total.py         100% linha por linha, distribuições, multi round-trip
test_concorrencia.py             10 threads simultâneas, freeze+thaw paralelos
test_predictor_edge_cases.py     variância zero, 256 cats, timestamps extremos
test_cluster_fault_tolerance.py  retry, sem workers, 10 jobs paralelos
test_formato_binario_spec.py     byte a byte do formato, SHA-256, sparse index
test_schema_detector_stress.py   50% campos ausentes, tipos misturados, 100 campos
test_cli_cobertura.py            todos os comandos CLI
test_performance_regression.py   ratio ≥8×, thaw <2s, audit <50ms
```

Status atual dos testes: [![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github&cacheSeconds=1)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)

---

## Docker — Cluster em produção

```bash
# Subir cluster com 4 workers
docker-compose up --scale worker=4

# Build local
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up --scale worker=2
```

Imagens disponíveis no Docker Hub:
- `caua-ferreira/permafrost-master`
- `caua-ferreira/permafrost-worker`

---

## Contribuindo

Contribuições são bem-vindas! Veja o [guia de contribuição](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md).

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Licença

Apache License 2.0 — veja [LICENSE](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE).

---

<div align="center">

Feito com ❄️ para dados que precisam durar décadas.

</div>

# ❄️ Permafrost Framework

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/permafrost-framework/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/permafrost-framework?color=blue)](https://pypi.org/project/permafrost-framework/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)

**Plataforma de compressão inteligente para arquivamento digital de longo prazo.**

*210 milhões de linhas: Permafrost + LZMA2 = 3,03 GB vs CSV = 16,35 GB (5,4×) — quase 2× melhor que Parquet. Consulte um único ano em 5 anos de dados: 42 milhões de linhas lidas, apenas 20% do arquivo tocado.*

🌐 [English](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.md) · **Português (BR)** · [Español](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.es.md) · [Français](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.fr.md) · [中文](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.zh-CN.md) · [العربية](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.ar.md) · [हिन्दी](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.hi.md)

[Documentação](https://caua-ferreira.github.io/permafrost-framework) · [Quick Start](#início-rápido) · [Benchmarks](#benchmarks) · [API](#referência-da-api)

</div>

---

## O que é o Permafrost?

Dados históricos corporativos — CSVs, JSONL, dumps de banco de dados — ficam em cold storage (S3 Glacier, Azure Archive) por anos a um custo elevado. O problema: se você precisa dos dados de um único mês em um arquivo de 10 GB, precisa descomprimir **tudo**.

O Permafrost resolve isso com dois mecanismos:

1. **Preditores colunares** — transforma semanticamente os dados antes da compressão (delta, zigzag, timestamps, categorias), atingindo ratios muito acima do LZMA2 puro
2. **Sparse index** — um índice embutido no arquivo que aponta para o byte exato de cada chunk, permitindo leituras seletivas via HTTP Range Requests sem baixar o arquivo inteiro

```
210.000.000 linhas × 13 colunas — benchmark real medido localmente:

CSV bruto:                 16,35 GB  (1,00×)
Parquet + Snappy:           5,89 GB  (2,78×)   gravação:  8,9 min
CSV + LZMA2 puro (p9):    ~3,80 GB  (~4,3×)   gravação:  ~7 h  ⚠️ impraticável
Permafrost + ZSTD:          3,25 GB  (5,03×)   gravação: 77,7 min
Permafrost + LZMA2:         3,03 GB  (5,40×)   gravação: 93,5 min   ← quase 2× melhor que Parquet

Consultar apenas o ano 2022 → 42M linhas em 5,7 min — apenas 20% do arquivo lido, 80% nunca tocado
```

---

## Funcionalidades

- **Alta compressão** — preditores colunares (delta_zigzag, lag1_zigzag, ts_delta_s, category_u8) antes de Zstd / LZMA2 / ZPAQ
- **Leituras seletivas** — sparse index embutido permite `filter={"ano": 2023}` sem descomprimir o resto
- **Integridade garantida** — SHA-256 por chunk, verificado antes de qualquer descompressão
- **Auto-descritivo** — schema Arrow completo embutido no arquivo; legível em 2040 sem documentação externa
- **Cloud-native** — suporte nativo para S3, Google Cloud Storage e Azure Blob Storage com HTTP Range Requests
- **Catálogo DuckDB** — busca de metadados em centenas de arquivos remotos sem baixar nenhum deles
- **Streaming** — processa datasets maiores que a RAM com `freeze_file()` e `peek()`
- **Cluster distribuído** — Master + Workers via FastAPI; processa 1 TB em paralelo com N workers
- **Criptografia** — AES-256-GCM por chunk, com overhead de armazenamento de 0,00%
- **CLI completo** — `permafrost freeze / unfreeze / audit / verify / catalog` com saída rica

---

## Instalação

```bash
# Instalação básica
pip install permafrost-framework

# Com suporte a AWS S3
pip install "permafrost-framework[s3]"

# Com suporte ao Google Cloud Storage
pip install "permafrost-framework[gcs]"

# Com suporte ao Azure Blob Storage
pip install "permafrost-framework[azure]"

# Todos os provedores de cloud
pip install "permafrost-framework[all-cloud]"
```

**Requisitos:** Python 3.10+

---

## Início Rápido

### Freeze e Unfreeze básico

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("historico_vendas.csv")

# Comprimir — retorna métricas
metrics = pf.freeze(df, "vendas.permafrost", codec=pf.CODEC_LZMA2, partition_by="ano")
print(f"Ratio: {metrics['ratio']:.2f}×  |  {metrics['original_mb']:.1f} MB → {metrics['stored_mb']:.1f} MB")

# Descomprimir tudo
df_back = pf.unfreeze("vendas.permafrost", verify=True)

# Descomprimir apenas 2023 — lê só os chunks daquele ano
df_2023 = pf.unfreeze("vendas.permafrost", filter={"ano": 2023})
```

### Streaming (datasets maiores que a RAM)

```python
# Comprimir um arquivo grande sem carregar na memória
pf.freeze_file("100gb.csv", "saida.permafrost", chunk_rows=50_000)

# Iterar em batches sem carregar tudo
for batch_df in pf.peek("saida.permafrost", batch_size=50_000):
    processar(batch_df)
```

### Cloud (S3, GCS, Azure)

```python
# Enviar direto para o S3
pf.freeze_to(df, "s3://meu-bucket/dados/vendas.permafrost")

# Leitura seletiva do S3 via HTTP Range Request — não baixa o arquivo inteiro
df_2023 = pf.thaw_from("s3://meu-bucket/dados/vendas.permafrost", filter={"ano": 2023})

# Auditoria remota sem baixar nada
info = pf.audit_remote("s3://meu-bucket/dados/vendas.permafrost")
```

### Catálogo — busca em múltiplos arquivos

```python
cat = pf.PermafrostCatalog("catalog.db")
cat.register_dir("s3://meu-bucket/cold/")   # indexa metadados sem baixar

# Buscar por nome, codec, lossless
results = cat.search(name="vendas", lossless_only=True)

# Relatório de custo estimado para Glacier Deep Archive
cat.cost_report("glacier_deep")

# Verificação de integridade em lote
cat.integrity_check()
```

### CLI

```bash
# Comprimir
permafrost freeze vendas.csv vendas.permafrost --codec lzma2 --partition-by ano

# Descomprimir com filtro
permafrost unfreeze vendas.permafrost --filter '{"ano": 2023}' --output vendas_2023.csv

# Auditar (sem descomprimir)
permafrost audit vendas.permafrost

# Verificar integridade de todos os chunks
permafrost verify vendas.permafrost
```

---

## Benchmarks

**Dataset:** 210.000.000 linhas × 13 colunas — dados corporativos reais (IDs, timestamps, categorias, floats, inteiros)
**Período:** 2020–2024, particionado por ano | **Medido localmente — não são estimativas.**

### Compressão vs. alternativas

| Formato | Tamanho | Ratio | Tempo de escrita |
|---------|---------|-------|-----------------|
| CSV bruto | 16,35 GB | 1,00× | — |
| Parquet + Snappy | 5,89 GB | 2,78× | 8,9 min |
| CSV + LZMA2 puro *(p9)* | ~3,80 GB | ~4,3× | **~7 h** ⚠️ |
| **Permafrost + ZSTD** | **3,25 GB** | **5,03×** | **77,7 min** |
| **Permafrost + LZMA2** | **3,03 GB** | **5,40×** | **93,5 min** |

### Leitura seletiva — Sparse Index

```python
# Ler apenas 2022 de um arquivo com 5 anos / 210M linhas de dados
df_2022 = pf.unfreeze("historico.permafrost", filter={"ano": 2022})
# 42.007.186 linhas em 5,7 min — apenas 20% do arquivo lido, 80% nunca tocado
```

### Custo de armazenamento cloud (S3 Glacier Deep Archive)

| Volume original | Sem Permafrost | Com Permafrost (5,4×) | Economia mensal |
|-----------------|----------------|----------------------|----------------|
| 1 TB | $0,99 | **$0,18** | **-81%** |
| 10 TB | $9,90 | **$1,83** | **-81%** |
| 100 TB | $99,00 | **$18,33** | **-81%** |

### Criptografia AES-256-GCM — overhead zero

Medido em 210M linhas reais:

| Operação | Sem criptografia | Com AES-256-GCM | Overhead |
|----------|-----------------|-----------------|----------|
| Freeze | 88,2 min | 77,9 min | **~0%** |
| Tamanho do arquivo | 3,250 GB | 3,250 GB | **+0,00%** |
| Unfreeze seletivo (42M linhas) | 2,0 min | 1,9 min | **~0%** |

O overhead real de armazenamento é 28 bytes por chunk (nonce + tag GCM) — 114,8 KB em um arquivo de 3,25 GB.

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

## Referência da API

### Core

| Função | Descrição |
|--------|-----------|
| `pf.freeze(df, path, ...)` | Comprime um DataFrame para um arquivo `.permafrost` |
| `pf.unfreeze(path, filter=None, verify=False)` | Descomprime; `filter` usa o sparse index |
| `pf.audit(path)` | Retorna metadados sem descomprimir |
| `pf.freeze_append(path, df_new)` | Adiciona linhas a um arquivo existente sem re-comprimir |

### Streaming

| Função | Descrição |
|--------|-----------|
| `pf.freeze_file(csv_path, out_path, chunk_rows=50_000)` | Comprime CSV grande sem carregar na memória |
| `pf.freeze_stream(cursor_gen, out_path)` | Comprime a partir de um gerador |
| `pf.peek(path, batch_size=50_000)` | Descomprime em batches iterativos |

### Cloud

| Função | Descrição |
|--------|-----------|
| `pf.freeze_to(df, uri)` | Comprime e envia direto para S3/GCS/Azure |
| `pf.thaw_from(uri, filter=None)` | Descomprime do cloud com Range Request |
| `pf.audit_remote(uri)` | Audita arquivo remoto sem baixar tudo |

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

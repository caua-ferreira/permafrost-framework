# ❄️ Permafrost Framework

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/permafrost-framework/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/permafrost-framework?color=blue)](https://pypi.org/project/permafrost-framework/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)

**Plataforma de compresión inteligente para archivado digital a largo plazo.**

*210 millones de filas: Permafrost + LZMA2 = 3,03 GB vs CSV = 16,35 GB (5,4×) — casi 2× mejor que Parquet. Consulta un solo año de 5 años de datos: 42M filas leídas, solo el 20% del archivo tocado.*

🌐 [English](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.md) · [Português (BR)](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.pt-BR.md) · **Español** · [Français](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.fr.md) · [中文](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.zh-CN.md) · [العربية](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.ar.md) · [हिन्दी](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.hi.md)

[Documentación](https://caua-ferreira.github.io/permafrost-framework) · [Inicio Rápido](#inicio-rápido) · [Benchmarks](#benchmarks) · [API](#referencia-de-api)

</div>

---

## ¿Qué es Permafrost?

Los datos históricos corporativos — CSVs, JSONL, volcados de bases de datos — se almacenan en cold storage (S3 Glacier, Azure Archive) durante años a un alto costo. El problema: si necesitas datos de un solo mes en un archivo de 10 GB, debes descomprimir **todo**.

Permafrost resuelve esto con dos mecanismos:

1. **Predictores de columna** — transforma semánticamente los datos antes de la compresión (delta, zigzag, timestamps, categorías), logrando ratios muy superiores al LZMA2 puro
2. **Sparse index** — un índice embebido en el archivo que apunta al byte exacto de cada chunk, permitiendo lecturas selectivas mediante HTTP Range Requests sin descargar el archivo completo

```
210.000.000 filas × 13 columnas — benchmark real medido localmente:

CSV sin comprimir:          16,35 GB  (1,00×)
Parquet + Snappy:            5,89 GB  (2,78×)   escritura:  8,9 min
CSV + LZMA2 puro (p9):     ~3,80 GB  (~4,3×)   escritura:  ~7 h  ⚠️ impracticable
Permafrost + ZSTD:           3,25 GB  (5,03×)   escritura: 77,7 min
Permafrost + LZMA2:          3,03 GB  (5,40×)   escritura: 93,5 min   ← casi 2× mejor que Parquet

Consultar solo el año 2022 → 42M filas en 5,7 min — solo el 20% del archivo leído
```

---

## Características

- **Alta compresión** — predictores de columna (delta_zigzag, lag1_zigzag, ts_delta_s, category_u8) antes de Zstd / LZMA2 / ZPAQ
- **Lecturas selectivas** — el sparse index permite `filter={"año": 2023}` sin descomprimir el resto
- **Integridad garantizada** — SHA-256 por chunk, verificado antes de cualquier descompresión
- **Auto-descriptivo** — esquema Arrow completo embebido en el archivo; legible en 2040 sin documentación externa
- **Cloud-native** — soporte nativo para S3, Google Cloud Storage y Azure Blob Storage con HTTP Range Requests
- **Catálogo DuckDB** — búsqueda de metadatos en cientos de archivos remotos sin descargar ninguno
- **Streaming** — procesa datasets más grandes que la RAM con `freeze_file()` y `peek()`
- **Clúster distribuido** — Master + Workers vía FastAPI; procesa 1 TB en paralelo con N workers
- **Cifrado** — AES-256-GCM por chunk, con overhead de almacenamiento de 0,00%
- **CLI completo** — `permafrost freeze / unfreeze / audit / verify / catalog`

---

## Instalación

```bash
# Instalación básica
pip install permafrost-framework

# Con soporte para AWS S3
pip install "permafrost-framework[s3]"

# Con soporte para Google Cloud Storage
pip install "permafrost-framework[gcs]"

# Con soporte para Azure Blob Storage
pip install "permafrost-framework[azure]"

# Todos los proveedores cloud
pip install "permafrost-framework[all-cloud]"
```

**Requisitos:** Python 3.10+

---

## Inicio Rápido

### Freeze y Unfreeze básico

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("historial_ventas.csv")

# Comprimir — devuelve métricas
metrics = pf.freeze(df, "ventas.permafrost", codec=pf.CODEC_LZMA2, partition_by="año")
print(f"Ratio: {metrics['ratio']:.2f}×  |  {metrics['original_mb']:.1f} MB → {metrics['stored_mb']:.1f} MB")

# Descomprimir todo
df_back = pf.unfreeze("ventas.permafrost", verify=True)

# Descomprimir solo 2023 — lee únicamente los chunks de ese año
df_2023 = pf.unfreeze("ventas.permafrost", filter={"año": 2023})
```

### Streaming (datasets más grandes que la RAM)

```python
# Comprimir un archivo grande sin cargarlo en memoria
pf.freeze_file("100gb.csv", "salida.permafrost", chunk_rows=50_000)

# Iterar en batches
for batch_df in pf.peek("salida.permafrost", batch_size=50_000):
    procesar(batch_df)
```

### Cloud (S3, GCS, Azure)

```python
# Subir directamente a S3
pf.freeze_to(df, "s3://mi-bucket/datos/ventas.permafrost")

# Lectura selectiva desde S3 — no descarga el archivo completo
df_2023 = pf.thaw_from("s3://mi-bucket/datos/ventas.permafrost", filter={"año": 2023})

# Auditoría remota sin descargar nada
info = pf.audit_remote("s3://mi-bucket/datos/ventas.permafrost")
```

### CLI

```bash
# Comprimir
permafrost freeze ventas.csv ventas.permafrost --codec lzma2 --partition-by año

# Descomprimir con filtro
permafrost unfreeze ventas.permafrost --filter '{"año": 2023}' --output ventas_2023.csv

# Auditar (sin descomprimir)
permafrost audit ventas.permafrost
```

---

## Benchmarks

### Compresión vs. alternativas

| Formato | Tamaño | Ratio | Tiempo de escritura |
|---------|--------|-------|---------------------|
| CSV sin comprimir | 16,35 GB | 1,00× | — |
| Parquet + Snappy | 5,89 GB | 2,78× | 8,9 min |
| CSV + LZMA2 puro *(p9)* | ~3,80 GB | ~4,3× | **~7 h** ⚠️ |
| **Permafrost + ZSTD** | **3,25 GB** | **5,03×** | **77,7 min** |
| **Permafrost + LZMA2** | **3,03 GB** | **5,40×** | **93,5 min** |

### Costo en cloud (S3 Glacier Deep Archive)

| Volumen original | Sin Permafrost | Con Permafrost (5,4×) | Ahorro mensual |
|-----------------|----------------|----------------------|----------------|
| 1 TB | $0,99 | **$0,18** | **-81%** |
| 10 TB | $9,90 | **$1,83** | **-81%** |
| 100 TB | $99,00 | **$18,33** | **-81%** |

---

## Referencia de API

| Función | Descripción |
|---------|-------------|
| `pf.freeze(df, path, ...)` | Comprime un DataFrame a `.permafrost` |
| `pf.unfreeze(path, filter=None)` | Descomprime; `filter` usa el sparse index |
| `pf.audit(path)` | Devuelve metadatos sin descomprimir |
| `pf.freeze_append(path, df_new)` | Añade filas a un archivo existente |
| `pf.peek(path, batch_size=50_000)` | Descomprime en batches iterativos |
| `pf.freeze_to(df, uri)` | Comprime y sube directamente a la nube |
| `pf.thaw_from(uri, filter=None)` | Descomprime desde la nube con Range Request |

---

## Contribuciones

¡Las contribuciones son bienvenidas! Consulta la [guía de contribución](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md).

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Licencia

Apache License 2.0 — ver [LICENSE](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE).

---

<div align="center">

Hecho con ❄️ para datos que necesitan durar décadas.

</div>

# API Reference — Core

## Funções principais

### `freeze()`

```python
permafrost.freeze(
    df: pd.DataFrame,
    path: str,
    codec: int = CODEC_LZMA2,
    quant: int = QUANT_NONE,
    chunk_rows: int = 10_000,
    partition_by: str | None = None,
    comment: str = "",
    retention_days: int = 0,
) -> dict
```

Comprime um DataFrame para o formato `.permafrost`.

**Parâmetros:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `df` | `pd.DataFrame` | — | Dataset a comprimir |
| `path` | `str` | — | Caminho de saída do arquivo |
| `codec` | `int` | `CODEC_LZMA2` | Algoritmo: `CODEC_LZMA2` ou `CODEC_ZSTD` |
| `quant` | `int` | `QUANT_NONE` | Quantização: `QUANT_NONE`, `QUANT_HIGH`, `QUANT_MEDIUM`, `QUANT_LOW` |
| `chunk_rows` | `int` | `10_000` | Linhas por chunk no arquivo |
| `partition_by` | `str \| None` | `None` | Coluna para sparse index |
| `comment` | `str` | `""` | Comentário embutido no header |
| `retention_days` | `int` | `0` | Dias de retenção (0 = permanente) |

**Retorna:** `dict` com métricas (`ratio`, `stored_mb`, `freeze_s`, `n_chunks`, ...)

---

### `thaw()`

```python
permafrost.thaw(
    path: str,
    verify: bool = True,
    filter: dict | None = None,
    row_range: tuple | None = None,
) -> pd.DataFrame
```

Descomprime um arquivo `.permafrost`.

**Parâmetros:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `path` | `str` | — | Arquivo `.permafrost` |
| `verify` | `bool` | `True` | Verificar SHA-256 antes de descomprimir |
| `filter` | `dict \| None` | `None` | Ex: `{"ano": 2023}` — thaw seletivo |
| `row_range` | `tuple \| None` | `None` | Ex: `(0, 9999)` — range de linhas |

---

### `audit()`

```python
permafrost.audit(path: str) -> dict
```

Lê metadados sem descomprimir nenhum chunk. Opera apenas no header e sparse index.

---

## Constantes de Codec

| Constante | Valor | Algoritmo |
|-----------|-------|-----------|
| `CODEC_ZSTD` | `0x01` | Zstandard L19 |
| `CODEC_LZMA2` | `0x02` | XZ/LZMA2 extreme |
| `CODEC_ZPAQ` | `0x03` | ZPAQ method=5 *(reservado)* |

## Constantes de Quantização

| Constante | Valor | Floats | Timestamps |
|-----------|-------|--------|------------|
| `QUANT_NONE` | `0x00` | exatos | exatos |
| `QUANT_HIGH` | `0x01` | 1 decimal | exatos |
| `QUANT_MEDIUM` | `0x02` | inteiro | floor(minuto) |
| `QUANT_LOW` | `0x03` | dezena | floor(hora) |

---

## Funções de Streaming

### `freeze_stream()`

```python
permafrost.freeze_stream(
    iterator,                          # Iterable[pd.DataFrame]
    path: str,
    schema_sample: pd.DataFrame | None = None,
    codec: int = CODEC_LZMA2,
    quant: int = QUANT_NONE,
    partition_by: str | None = None,
    comment: str = "",
    progress_cb: callable | None = None,
) -> dict
```

### `freeze_file()`

```python
permafrost.freeze_file(
    input_path: str,                   # .csv, .jsonl, .parquet
    output_path: str | None = None,
    codec: int = CODEC_LZMA2,
    quant: int = QUANT_NONE,
    chunk_rows: int = 50_000,
    partition_by: str | None = None,
    comment: str = "",
    progress_cb: callable | None = None,
) -> dict
```

### `thaw_iter()`

```python
permafrost.thaw_iter(
    path: str,
    verify: bool = True,
    filter: dict | None = None,
    batch_size: int | None = None,     # None = 1 chunk por iteração
) -> Iterator[pd.DataFrame]
```

---

## SchemaDetector

```python
det = permafrost.SchemaDetector(sample_size=500)

# Detectar de arquivo
df, dtype, manifest = det.detect("dados.jsonl")
# dtype: DataType.TABULAR | DataType.SEMI_STRUCTURED

# Detectar de list[dict]
df, dtype, manifest = det.flatten(lista_de_docs)

# Tipos de DataType
permafrost.DataType.TABULAR          # CSV, DataFrame
permafrost.DataType.SEMI_STRUCT      # JSONL, MongoDB
permafrost.DataType.TEXT_STREAM      # logs, emails
```

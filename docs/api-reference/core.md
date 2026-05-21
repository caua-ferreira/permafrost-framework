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

### `unfreeze()`

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

### `peek()`

```python
permafrost.peek(
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

---

### `append()` / `freeze_append()`

```python
permafrost.append(
    path: str,
    df: pd.DataFrame,
    verify: bool = True,
    chunk_rows: int = 10_000,
) -> dict
```

Adiciona `df` a um arquivo `.permafrost` existente **sem reescrever os chunks originais**. Faz patch no header (`n_chunks`, `orig_rows`) e atualiza o sparse index.

**Parâmetros:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `path` | `str` | — | Arquivo `.permafrost` / `.pf` existente |
| `df` | `pd.DataFrame` | — | Novos dados (schema deve ser compatível) |
| `verify` | `bool` | `True` | Verificar SHA-256 dos chunks existentes antes de escrever |
| `chunk_rows` | `int` | `10_000` | Linhas por chunk nos novos dados |

**Retorna:** `dict` com `total_rows`, `new_chunks`, `path`.

---

### `diff()`

```python
permafrost.diff(
    path_v1: str,
    path_v2: str,
    on: str | list[str] | None = None,
    output: str = "dict",
    include: list[str] | None = None,
    changed_columns_only: bool = False,
    rtol: float = 1e-9,
    verify: bool = True,
) -> dict | pd.DataFrame
```

Compara dois arquivos e retorna as diferenças linha a linha.

**Parâmetros:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `path_v1` | `str` | — | Arquivo "antes" |
| `path_v2` | `str` | — | Arquivo "depois" |
| `on` | `str \| list \| None` | `None` | Coluna(s) de join. Se `None`, usa `primary_key` do arquivo ou fallback posicional |
| `output` | `str` | `"dict"` | `"dict"` · `"dataframe"` · `"summary"` |
| `include` | `list \| None` | `None` | Filtrar tipo: `["inserted"]`, `["deleted","changed"]`, etc. |
| `changed_columns_only` | `bool` | `False` | No DataFrame de alterados, incluir só colunas que mudaram |
| `rtol` | `float` | `1e-9` | Tolerância relativa para comparação de floats |

**Output `"dict"`:** `{"inserted": df, "deleted": df, "changed": df, "unchanged_count": int, "summary": dict}`

**Output `"dataframe"`:** único DataFrame com coluna `_diff` (`"inserted"` / `"deleted"` / `"changed"`)

**Output `"summary"`:** apenas contagens `{"inserted": n, "deleted": n, "changed": n, "unchanged": n}`

---

### `unfreeze()` — parâmetros adicionais v1.2.x

```python
permafrost.unfreeze(
    path: str,
    verify: bool = True,
    filter: dict | None = None,
    row_range: tuple | None = None,
    output_format: str = "dataframe",   # novo em v1.2.0
    sep: str = ",",                     # novo em v1.2.0
) -> pd.DataFrame | bytes
```

| `output_format` | Retorno | Notas |
|----------------|---------|-------|
| `"dataframe"` | `pd.DataFrame` | padrão |
| `"records"` | `list[dict]` | cada linha como dicionário |
| `"json"` | `bytes` | JSON serializado |
| `"csv"` | `bytes` | CSV; use `sep=` para separador customizado |
| `"parquet"` | `bytes` | Parquet sem escrever arquivo |
| `"xlsx"` | `bytes` | Excel (requer `openpyxl`) |

---

### `query()`

```python
permafrost.query(sql: str) -> pd.DataFrame
```

Executa SQL arbitrário sobre um ou mais arquivos `.permafrost`/`.pf` usando DuckDB. Os arquivos são referenciados diretamente na query como strings de caminho.

```python
df = pf.query("SELECT * FROM 'vendas.pf' WHERE ano = 2024 LIMIT 100")

# JOIN entre arquivos
df = pf.query("""
    SELECT a.id, a.total, b.meta
    FROM 'vendas.pf' a
    JOIN 'metas.pf'  b ON a.regiao = b.regiao
""")
```

**Registrar alias:**

```python
pf.register("vendas", "vendas.pf")
df = pf.query("SELECT * FROM vendas WHERE total > 1000")
pf.unregister("vendas")
```

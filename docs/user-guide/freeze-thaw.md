# Freeze & Thaw — API Core

---

## freeze()

Comprime um `pd.DataFrame` para o formato `.permafrost`.

```python
metrics = pf.freeze(
    df,                          # pd.DataFrame
    "arquivo.permafrost",        # caminho de saída
    codec=pf.CODEC_LZMA2,       # codec de compressão
    quant=pf.QUANT_NONE,        # nível de quantização
    partition_by="ano",          # coluna de partição (opcional)
    chunk_rows=10_000,           # linhas por chunk
    comment="meu dataset",       # comentário embutido no arquivo
    retention_days=0,            # 0 = sem data de expiração
)
```

### Codecs disponíveis

| Constante | Algoritmo | Quando usar |
|-----------|-----------|-------------|
| `CODEC_LZMA2` | XZ/LZMA2 extreme | Cold storage — melhor ratio, aceita freeze mais lento |
| `CODEC_ZSTD` | Zstandard L19 | Warm storage — decompress 6× mais rápido que LZMA2 |

### Preditores colunares

O Permafrost detecta automaticamente o melhor encoding para cada coluna:

| Tipo de dado | Preditor | Mecanismo | Ganho típico |
|---|---|---|---|
| IDs sequenciais | `delta_zigzag` | Armazena diferenças entre valores | 4–8× |
| Floats monetários | `lag1_zigzag` | Armazena resíduo vs valor anterior | 3–6× |
| Timestamps | `ts_delta_s` | Delta em segundos Unix | 4–8× |
| Categorias (≤256) | `category_u8` | Índice de 1 byte por valor | 6–12× |
| Texto livre | `raw_text` | UTF-8 com separador `\x00` | 2–4× |

```python
# Ver qual preditor foi escolhido para cada coluna
info = pf.audit("arquivo.permafrost")
for col, manifest in info["col_predictors"].items():
    print(f"  {col}: {manifest}")
```

### Retorno: métricas do freeze

```python
{
    "path":           "arquivo.permafrost",
    "rows":           80000,
    "cols":           9,
    "n_chunks":       16,
    "chunk_rows":     5000,
    "original_mb":    5.85,
    "stored_mb":      0.678,
    "ratio":          8.37,
    "reduction_pct":  88.0,
    "freeze_s":       2.23,
    "codec":          "lzma2",
    "partition_by":   "ano",
    "index_entries":  16,
}
```

---

## unfreeze()

Descomprime um arquivo `.permafrost` de volta para `pd.DataFrame`.

```python
# Thaw completo
df = pf.unfreeze("arquivo.permafrost", verify=True)

# Thaw seletivo por partição
df_2023 = pf.unfreeze("arquivo.permafrost", filter={"ano": 2023})

# Thaw por range de linhas
df_sample = pf.unfreeze("arquivo.permafrost", row_range=(0, 9_999))

# Sem verificação SHA-256 (mais rápido, para dados confiáveis)
df = pf.unfreeze("arquivo.permafrost", verify=False)
```

### Como o sparse index funciona

```
arquivo.permafrost (footer)
    ...
    [SPARSE INDEX JSON]    ← byte_offset de cada chunk
    [INDEX_LEN: 4B]
    [INDEX_SHA256: 32B]
    [EOF: "SMRP" 4B]      ← leitura de trás para frente

thaw(filter={"ano": 2021}):
  1. Lê os últimos ~8KB (sparse index)
  2. Filtra chunks onde part_key contém "2021"
  3. Lê APENAS esses bytes do arquivo
  4. Descomprime e retorna
```

!!! success "Benchmark"
    Para um arquivo com 5 anos de dados, `filter={"ano": 2021}` lê
    **12–31% do arquivo** em vez de 100%.

---

## audit()

Lê metadados sem descomprimir nenhum chunk.

```python
info = pf.audit("arquivo.permafrost")
```

Retorna:

```python
{
    "version":         "1.2",
    "codec":           "lzma2",
    "quant":           0,           # 0=lossless
    "freeze_date":     "2026-05-13T14:30:00",
    "orig_rows":       80000,
    "n_chunks":        16,
    "chunk_rows":      5000,
    "file_size_mb":    0.678,
    "columns":         ["id", "data", "ano", ...],
    "partition_col":   "ano",
    "partition_keys":  ["2020", "2021", "2022", ...],
    "comment":         "Vendas 2022-2024",
    "index_entries":   [...],       # sparse index completo
}
```

---

## Integridade e Bit-rot

O Permafrost verifica SHA-256 em 3 camadas:

```
[HEADER SHA-256]  ← verificado antes de parsear o header
[CHUNK SHA-256]   ← verificado antes de descomprimir cada chunk
[INDEX SHA-256]   ← verificado antes de usar o sparse index
```

```python
# Corrupção é detectada ANTES da decompressão:
try:
    df = pf.unfreeze("arquivo.permafrost", verify=True)
except ValueError as e:
    print(e)
    # "Header SHA-256 inválido — arquivo modificado"
    # "Chunk 3 corrompido — SHA-256 não confere"
    # "EOF magic ausente — arquivo truncado"
```

!!! warning "Cenários detectados"
    - Bit-rot em storage (disco com setores ruins)
    - Transferência parcial/corrompida
    - Arquivo modificado acidentalmente
    - Truncamento por falta de espaço em disco

# PermafrostCatalog — Índice Centralizado (DuckDB)

Com dezenas de arquivos `.permafrost`, você precisa saber: "quais têm dados de 2022?",
"qual tem a coluna `total`?", "quanto estou gastando?". O Catalog resolve isso.

---

## Criar e registrar

```python
import permafrost as pf

cat = pf.PermafrostCatalog(".permafrost_catalog.db")

# Registrar um arquivo (lê apenas header + footer — zero decompressão)
cat.register("vendas_2022.permafrost")
cat.register("vendas_2023.permafrost", tags=["producao", "fiscal"])

# Registrar diretório inteiro
cat.register_dir("/dados/cold/", tags=["producao"], recursive=True)
```

O registro é **idempotente** — chamar `register()` duas vezes no mesmo arquivo
não duplica o registro.

---

## Buscar datasets

```python
# Busca por nome (substring)
df = cat.search(name="vendas")

# Filtrar por codec
df = cat.search(codec="lzma2")

# Filtrar por chave de partição
df = cat.search(partition_key="2023")

# Filtrar por coluna presente
df = cat.search(columns_contain="total")

# Apenas lossless
df = cat.search(lossless_only=True)

# Combinar filtros
df = cat.search(
    name="vendas",
    codec="lzma2",
    partition_key="2023",
    min_rows=50_000,
    lossless_only=True,
)

print(df[["name", "codec", "rows", "mb", "partition_col"]])
```

---

## Thaw via catalog

```python
# O catalog encontra o arquivo e roteia o thaw automaticamente
df = cat.thaw("vendas_2023", filter={"ano": 2023})
```

---

## Inspecionar chunks

```python
# Chunks de um dataset com filtro por partition key
df_chunks = cat.search_chunks("vendas_2023", part_key="2023")
print(df_chunks[["chunk_id", "row_start", "row_end", "part_key", "byte_offset", "kb"]])
```

---

## Relatório de custo

```python
# Custo estimado por dataset
cr = cat.cost_report("glacier_deep")  # ou "s3_standard", "s3_ia", "glacier"
print(cr[["name", "size_mb", "cost_monthly_usd", "cost_annual_usd", "cost_3yr_usd"]])

# Total
print(f"Total mensal: ${cr['cost_monthly_usd'].sum():.4f}")
print(f"Total 3 anos: ${cr['cost_3yr_usd'].sum():.2f}")
```

---

## Verificação de integridade

```python
# Verifica SHA-256 de todos os chunks de todos os datasets
# Não descomprime — apenas confere os hashes
ic = cat.integrity_check()

print(ic[["name", "status", "chunks_ok", "chunks_fail"]])
# name           status  chunks_ok  chunks_fail
# vendas_2022    OK      16         0
# vendas_2023    OK      18         0
# clientes_v1    OK      4          0
```

---

## SQL direto (DuckDB)

```python
# Qualquer query DuckDB nas tabelas internas
df = cat.sql("""
    SELECT
        codec,
        COUNT(*) as datasets,
        SUM(orig_rows) as total_linhas,
        ROUND(SUM(file_size_mb), 2) as total_mb,
        ROUND(AVG(file_size_mb / orig_rows * 1000), 4) as mb_per_1k_rows
    FROM datasets
    GROUP BY codec
    ORDER BY total_mb DESC
""")

# JOIN com chunks para análise granular
df = cat.sql("""
    SELECT d.name, c.part_key, c.row_start, c.row_end,
           ROUND(c.byte_len / 1024.0, 1) as kb
    FROM datasets d
    JOIN chunks c ON c.dataset_id = d.id
    WHERE d.name LIKE '%vendas%'
    ORDER BY c.chunk_id
""")
```

---

## Schema do banco DuckDB

### Tabela `datasets`

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| id | INTEGER PK | |
| name | VARCHAR | Nome derivado do filename |
| path | VARCHAR UNIQUE | Caminho absoluto |
| freeze_date | TIMESTAMP | |
| codec | VARCHAR | lzma2 / zstd |
| quant_level | INTEGER | 0=lossless, 1=high, 2=medium |
| orig_rows | BIGINT | |
| n_chunks | INTEGER | |
| file_size_mb | DOUBLE | |
| partition_col | VARCHAR | |
| partition_keys | VARCHAR | JSON array |
| columns | VARCHAR | JSON array |
| tags | VARCHAR | JSON array |
| schema_hash | VARCHAR | SHA-256 dos nomes das colunas |
| verified_ok | BOOLEAN | Última verificação de integridade |

### Tabela `chunks`

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| id | INTEGER PK | |
| dataset_id | INTEGER FK → datasets | |
| chunk_id | INTEGER | Índice do chunk |
| row_start | BIGINT | |
| row_end | BIGINT | |
| part_key | VARCHAR | Valor da partition key |
| byte_offset | BIGINT | Posição no arquivo para seek |
| byte_len | BIGINT | |
| sha256 | VARCHAR | |

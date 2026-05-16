# API Reference — PermafrostContext

`PermafrostContext` é a API de alto nível do Permafrost para v1.0.
Unifica catalog + storage + cluster em um único objeto configurável.

---

## Criação

```python
import permafrost as pf

# Apenas local (mais simples)
ctx = pf.PermafrostContext(catalog="catalog.db")

# Com cloud storage
ctx = pf.PermafrostContext(
    catalog="catalog.db",
    storage="s3://meu-bucket/cold/",
)

# Completo (catalog + storage + cluster)
ctx = pf.PermafrostContext(
    catalog="catalog.db",
    storage="s3://meu-bucket/cold/",
    cluster="http://master:8700",
    codec=pf.CODEC_ZSTD,
    token="eyJ...",
)

# Como context manager (fecha conexões automaticamente)
with pf.PermafrostContext(catalog="catalog.db") as ctx:
    ctx.freeze(df, "vendas_2024")
    df = ctx.thaw("vendas_2024")
```

---

## Parâmetros do construtor

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `catalog` | `str \| None` | `None` | Caminho do banco DuckDB do catalog |
| `storage` | `str \| None` | `None` | URI base do storage (`s3://`, `gs://`, `azure://`, local) |
| `cluster` | `str \| None` | `None` | URL do PermafrostMaster |
| `codec` | `int \| str` | `CODEC_LZMA2` | Codec padrão para freeze |
| `quant` | `int` | `QUANT_NONE` | Nível de quantização padrão |
| `key` | `bytes \| KeyProvider \| None` | `None` | Chave AES-256 (32 bytes) ou KeyProvider |
| `token` | `str \| None` | `None` | Token JWT para autenticação no cluster |
| `**storage_kwargs` | `dict` | `{}` | Parâmetros extras para o StorageAdapter |

---

## Métodos

### `freeze(df, name, **kwargs) → dict`

Comprime um DataFrame e armazena no storage configurado.

- Se **catalog** estiver configurado → registra automaticamente
- Se **cluster** estiver configurado → delega o job ao master
- Suporta todos os parâmetros de [`freeze()`](core.md)

```python
# Básico
metrics = ctx.freeze(df, "vendas_2024")

# Com opções
metrics = ctx.freeze(df, "vendas_2024",
    partition_by="ano",
    codec=pf.CODEC_ZSTD,
    chunk_rows=50_000,
)

print(f"Ratio: {metrics['ratio']:.2f}×")
print(f"URI: {metrics['uri']}")
```

**Retorno:** dicionário com `ratio`, `rows`, `stored_mb`, `uri`, `n_chunks`, etc.

---

### `thaw(name, **kwargs) → pd.DataFrame`

Descomprime um arquivo do storage configurado.

```python
# Completo
df = ctx.thaw("vendas_2024")

# Com filtro (usa sparse index — sem descomprimir chunks desnecessários)
df_2023 = ctx.thaw("vendas_2024", filter={"ano": 2023})

# Sem verificação SHA-256 (mais rápido)
df = ctx.thaw("vendas_2024", verify=False)
```

---

### `audit(name) → dict`

Inspeciona metadados sem descomprimir. Usa range requests para storage remoto.

```python
info = ctx.audit("vendas_2024")
print(info["codec"])       # "lzma2"
print(info["orig_rows"])   # 80000
print(info["n_chunks"])    # 8
```

---

### `list(pattern="*.permafrost") → list[str]`

Lista arquivos no storage configurado.

```python
arquivos = ctx.list()
# ["s3://bucket/cold/vendas_2024.permafrost", ...]
```

---

### `freeze_async(df, name, **kwargs) → str`

Submete um job ao cluster e retorna `job_id` imediatamente.

```python
job_id = ctx.freeze_async(df, "vendas_grandes")
# ... fazer outras coisas ...
result = ctx.wait(job_id)
print(result["ratio"])  # 10.2
```

Requer `cluster` configurado.

---

### `wait(job_id, poll_interval=2.0) → dict`

Aguarda a conclusão de um job do cluster.

---

### Métodos de catalog

Todos delegam para [`PermafrostCatalog`](catalog.md). Requerem `catalog` configurado.

| Método | Descrição |
|--------|-----------|
| `ctx.register(path, tags=None)` | Registra um arquivo local no catalog |
| `ctx.search(**kwargs)` | Busca datasets com filtros |
| `ctx.cost_report(tier="glacier_deep")` | Custo estimado por tier |
| `ctx.integrity_check(name_filter=None)` | Verifica SHA-256 de todos os datasets |
| `ctx.stats()` | Métricas agregadas (total_datasets, total_mb, etc.) |
| `ctx.sql(query)` | SQL direto no DuckDB do catalog |

```python
# Exemplos
ctx.search(name="vendas", lossless_only=True)
ctx.search(codec="zstd", min_rows=10_000)
ctx.cost_report("glacier_deep")
ctx.sql("SELECT codec, COUNT(*) FROM datasets GROUP BY codec")
```

---

## Resolução de URIs

Quando `storage` está configurado, o `name` é resolvido em relação ao URI base:

```
storage = "s3://meu-bucket/cold/"
name    = "vendas_2024"
→ URI   = "s3://meu-bucket/cold/vendas_2024.permafrost"
```

A extensão `.permafrost` é adicionada automaticamente se ausente.

---

## Exemplo completo

```python
import permafrost as pf
import pandas as pd

# Criar contexto completo
ctx = pf.PermafrostContext(
    catalog="catalog.db",
    storage="s3://meu-bucket/cold/",
    cluster="http://master:8700",
    codec=pf.CODEC_LZMA2,
    token="eyJhbGciOiJIUzI1NiJ9...",
)

# Carregar dados
df = pd.read_csv("vendas_historico.csv")

# Freeze + upload + catalog automático
metrics = ctx.freeze(df, "vendas_2024", partition_by="ano")
print(f"Comprimido: {metrics['ratio']:.1f}× | URI: {metrics['uri']}")

# Leitura seletiva (sem baixar o arquivo inteiro)
df_2023 = ctx.thaw("vendas_2024", filter={"ano": 2023})

# Buscar no catalog
resultados = ctx.search(name="vendas", lossless_only=True)

# Custo estimado
relatorio = ctx.cost_report("glacier_deep")
print(f"${relatorio['cost_monthly_usd'].sum():.4f}/mês")

# Fechar conexões
ctx.close()
```

# Getting Started

Este tutorial leva você do zero ao primeiro arquivo `.permafrost` em 5 minutos.

---

## 1. Instalação

```bash
pip install permafrost-framework
```

**Extras opcionais (cloud storage):**

```bash
pip install permafrost-framework[s3]        # AWS S3
pip install permafrost-framework[gcs]       # Google Cloud Storage
pip install permafrost-framework[azure]     # Azure Blob Storage
pip install permafrost-framework[all-cloud] # todos
```

Verifique a instalação:

```python
import permafrost as pf
print(pf.__version__)   # 1.0.1
```

---

## 2. Primeiro freeze

```python
import permafrost as pf
import pandas as pd
import numpy as np

# Criar um dataset de exemplo
np.random.seed(42)
N = 50_000
df = pd.DataFrame({
    "id":     np.arange(1, N+1, dtype="int32"),
    "data":   pd.date_range("2022-01-01", periods=N, freq="30min"),
    "ano":    pd.date_range("2022-01-01", periods=N, freq="30min").year,
    "regiao": np.random.choice(["Norte","Sul","Leste","Oeste"], N),
    "total":  np.round(np.random.uniform(1, 50000, N), 2),
    "status": np.random.choice(["Ativo","Cancelado","Pendente"], N),
})

# Ordenar pela coluna de partição (importante para o sparse index)
df = df.sort_values("ano").reset_index(drop=True)

# Comprimir
metrics = pf.freeze(
    df,
    "vendas.permafrost",
    codec=pf.CODEC_LZMA2,      # melhor ratio para cold data
    quant=pf.QUANT_NONE,       # lossless (sem perda)
    partition_by="ano",        # habilita thaw seletivo por ano
    chunk_rows=10_000,         # linhas por chunk
    comment="Vendas 2022-2024" # metadado livre
)

print(f"Original: {metrics['original_mb']:.2f} MB")
print(f"Arquivo:  {metrics['stored_mb']:.3f} MB")
print(f"Ratio:    {metrics['ratio']:.2f}×")
print(f"Redução:  {metrics['reduction_pct']:.1f}%")
print(f"Tempo:    {metrics['freeze_s']:.2f}s")
```

```
Original: 3.62 MB
Arquivo:  0.423 MB
Ratio:    8.56×
Redução:  88.3%
Tempo:    1.84s
```

---

## 3. Inspecionar sem descomprimir

```python
info = pf.audit("vendas.permafrost")

print(f"Versão:       {info['version']}")
print(f"Codec:        {info['codec']}")
print(f"Linhas:       {info['orig_rows']:,}")
print(f"Chunks:       {info['n_chunks']}")
print(f"Partição:     {info['partition_col']}")
print(f"Anos:         {info['partition_keys']}")
print(f"Colunas:      {info['columns']}")
print(f"Freeze em:    {info['freeze_date']}")
print(f"Comentário:   {info['comment']}")
```

!!! tip "Zero decompressão"
    `audit()` lê apenas o header e o sparse index — os últimos bytes do arquivo.
    Um arquivo de 2 GB é auditado em < 1ms.

---

## 4. Descomprimir (thaw)

### Thaw completo

```python
df_back = pf.unfreeze("vendas.permafrost", verify=True)
print(f"{len(df_back):,} linhas recuperadas")
```

### Thaw seletivo — ler só 1 ano

```python
df_2023 = pf.unfreeze("vendas.permafrost", filter={"ano": 2023})
# Lê apenas 12–31% do arquivo — não descomprime os outros anos
```

### Thaw por range de linhas

```python
df_sample = pf.unfreeze("vendas.permafrost", row_range=(0, 9_999))
# Primeiras 10.000 linhas
```

---

## 5. Verificar integridade

```python
# verify=True (padrão) verifica SHA-256 de cada chunk antes de descomprimir
df = pf.unfreeze("vendas.permafrost", verify=True)

# Verificação standalone (sem descomprimir)
import hashlib, struct

with open("vendas.permafrost", "rb") as f:
    raw = f.read()

assert raw[:4] == b"PRMS", "Magic inválido"
assert raw[-4:] == b"SMRP", "EOF corrompido"
print("✓ Arquivo íntegro")
```

---

## 6. Vault mode (semi-lossy, ratio maior)

Para dados que só precisam de precisão razoável (compliance de longo prazo):

```python
metrics_vault = pf.freeze(
    df,
    "vendas_vault.permafrost",
    quant=pf.QUANT_MEDIUM   # floats arredondados para inteiro, timestamps para minuto
)

print(f"Vault ratio: {metrics_vault['ratio']:.2f}×")  # ~10×+ vs ~8.5× lossless
```

| Quant | Floats | Timestamps | Uso |
|-------|--------|------------|-----|
| `QUANT_NONE` | exatos | exatos | backup, compliance que precisa de precisão |
| `QUANT_HIGH` | 1 decimal | exatos | analytics histórico |
| `QUANT_MEDIUM` | inteiro | floor(minuto) | cold storage de longo prazo |
| `QUANT_LOW` | dezena | floor(hora) | arquivamento extremo |

---

## 7. Encryption at rest (AES-256-GCM)

```python
key = b"my-secret-key-exactly-32-bytes!!"

# Freeze with encryption — same API, just add key=
pf.freeze(df, "sensitive.permafrost", key=key)

# Thaw — provide the same key
df_back = pf.unfreeze("sensitive.permafrost", key=key)

# Selective read works on encrypted files too
df_2023 = pf.unfreeze("sensitive.permafrost", filter={"ano": 2023}, key=key)
```

Encryption is per-chunk (AES-256-GCM with unique nonce per chunk), so sparse index reads remain possible even on encrypted files. For production, use a [KMS provider](user-guide/encryption.md) instead of a raw key.

---

## 8. PermafrostContext — API unificada (v1.0)

Para workflows mais completos, use `PermafrostContext` em vez de chamar
`freeze()`, `thaw()`, `audit()` e `PermafrostCatalog` separadamente:

```python
# Tudo em um objeto — catalog + storage + cluster
ctx = pf.PermafrostContext(
    catalog="catalog.db",
    storage="s3://meu-bucket/cold/",   # opcional
)

# Freeze + upload + catalog register em uma linha
metrics = ctx.freeze(df, "vendas_2024", partition_by="ano")

# Thaw com filtro
df_2023 = ctx.unfreeze("vendas_2024", filter={"ano": 2023})

# Buscar no catalog
ctx.search(name="vendas", lossless_only=True)

# Custo estimado
ctx.cost_report("glacier_deep")

# Context manager fecha conexões automaticamente
with pf.PermafrostContext(catalog="catalog.db") as ctx:
    ctx.freeze(df, "backup_2024")
```

Veja a [referência completa do PermafrostContext](api-reference/context.md).

---

## 9. Próximos passos

<div class="grid cards" markdown>

- :material-layers: **[PermafrostContext](api-reference/context.md)**
  — API unificada para catalog + storage + cluster

- :material-database: **[Dados SQL & NoSQL](user-guide/nosql.md)**
  — CSV, JSONL, MongoDB, DynamoDB

- :material-cloud: **[Cloud Storage](user-guide/cloud.md)**
  — S3, GCS, Azure, audit sem download

- :material-waves: **[Streaming](user-guide/streaming.md)**
  — Datasets maiores que a RAM

- :material-server-network: **[Cluster](user-guide/cluster.md)**
  — Processamento distribuído

- :material-lock: **[Encryption](user-guide/encryption.md)**
  — AES-256-GCM per chunk, KMS providers

</div>

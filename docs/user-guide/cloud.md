# Cloud Storage — S3, GCS, Azure

Freeze e thaw direto para cloud, sem arquivo intermediário.
Audit remoto via range requests — sem baixar o arquivo inteiro.

---

## AWS S3

### Configuração

```bash
pip install permafrost-framework[s3]

# Autenticação (qualquer um dos métodos abaixo):
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
# ou ~/.aws/credentials
# ou IAM Role (EC2/ECS/Lambda — automático)
```

### Freeze direto para S3

```python
import permafrost as pf

# Freeze e upload em uma chamada
metrics = pf.freeze_to(
    df,
    "s3://meu-bucket/dados/vendas_2024.permafrost",
    codec=pf.CODEC_LZMA2,
    partition_by="ano",
)

print(f"Upload: {metrics['stored_mb']:.2f} MB → s3://...")
```

### Thaw seletivo da cloud

```python
# Baixa apenas os chunks necessários
df_2024 = pf.thaw_from(
    "s3://meu-bucket/dados/vendas_2024.permafrost",
    filter={"ano": 2024},
)
```

### Audit remoto (range requests — sem download total)

```python
# Baixa apenas ~12KB (header + footer) do arquivo
info = pf.audit_remote("s3://meu-bucket/dados/vendas_2024.permafrost")

print(f"Linhas: {info['orig_rows']:,}")
print(f"Codec:  {info['codec']}")
print(f"Anos:   {info['partition_keys']}")
# Para um arquivo de 2 GB, baixamos apenas ~12KB
```

### Adapter S3 direto

```python
from permafrost import S3Adapter

# S3 padrão
s3 = S3Adapter()

# S3 com região específica
s3 = S3Adapter(region="us-east-1")

# MinIO ou S3-compatible
s3 = S3Adapter(endpoint_url="http://localhost:9000")

# Glacier Deep Archive
s3 = S3Adapter(storage_class="GLACIER_DEEP_ARCHIVE")

# Upload com verificação
result = s3.upload_and_verify("local.permafrost", "s3://bucket/arquivo.permafrost")
print(result["remote_magic_ok"])  # True

# Listar arquivos .permafrost no bucket
files = s3.list("s3://bucket/dados/")
for f in files:
    print(f)

# Configurar lifecycle para Glacier Deep Archive automático
s3.set_lifecycle(
    bucket="meu-bucket",
    prefix="dados/",
    transition_days=30,              # após 30 dias → Glacier
    target_class="GLACIER_DEEP_ARCHIVE"
)
```

---

## Google Cloud Storage

```bash
pip install permafrost-framework[gcs]
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
```

```python
import permafrost as pf

pf.freeze_to(df, "gs://meu-bucket/vendas.permafrost")
df = pf.thaw_from("gs://meu-bucket/vendas.permafrost", filter={"ano": 2024})
info = pf.audit_remote("gs://meu-bucket/vendas.permafrost")
```

---

## Azure Blob Storage

```bash
pip install permafrost-framework[azure]
```

```python
import permafrost as pf
from permafrost import AzureAdapter

azure = AzureAdapter(conn_str="DefaultEndpointsProtocol=https;AccountName=...")

pf.freeze_to(df, "azure://meu-container/vendas.permafrost", adapter=azure)
df = pf.thaw_from("azure://meu-container/vendas.permafrost", adapter=azure)
```

---

## Factory automático

```python
import permafrost as pf

# O adapter correto é criado automaticamente pela URI
adapter = pf.storage_from_uri("s3://bucket/")     # → S3Adapter
adapter = pf.storage_from_uri("gs://bucket/")     # → GCSAdapter
adapter = pf.storage_from_uri("azure://cont/")    # → AzureAdapter
adapter = pf.storage_from_uri("/local/path/")     # → LocalAdapter
```

---

## Custo — Glacier Deep Archive

Exemplo para 1 TB de dados corporativos originais:

| Situação | Tamanho | Custo/mês (Glacier Deep) |
|----------|---------|--------------------------|
| Sem Permafrost | 1 TB | $0.99 |
| Com Permafrost (ratio 8×) | ~125 GB | **$0.12** |
| **Economia anual** | — | **$10.44** |

Para 100 TB: economia de ~$1.044/ano.

```python
# Relatório de custo no catalog
cat = pf.PermafrostCatalog(".permafrost_catalog.db")
cat.register_dir("s3://bucket/dados/")

cr = cat.cost_report("glacier_deep")
print(cr[["name", "size_mb", "cost_monthly_usd", "cost_annual_usd"]])
```

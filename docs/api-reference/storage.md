# API Reference — Storage Adapters

## freeze_to() / thaw_from() / audit_remote()

```python
# Freeze direto para cloud
permafrost.freeze_to(
    df: pd.DataFrame,
    remote_uri: str,            # "s3://bucket/arquivo.permafrost"
    adapter: StorageAdapter | None = None,  # auto-detectado se None
    tmp_dir: str = "/tmp",
    keep_local: bool = False,
    **freeze_kwargs,            # repassados para freeze()
) -> dict

# Thaw da cloud
permafrost.thaw_from(
    remote_uri: str,
    adapter: StorageAdapter | None = None,
    tmp_dir: str = "/tmp",
    keep_local: bool = False,
    **thaw_kwargs,              # repassados para thaw()
) -> pd.DataFrame

# Audit sem download total (range requests)
permafrost.audit_remote(
    remote_uri: str,
    adapter: StorageAdapter | None = None,
) -> dict
```

---

## storage_from_uri()

```python
adapter = permafrost.storage_from_uri(
    uri: str,     # "s3://bucket/", "gs://bucket/", "azure://cont/", "/local/"
    **kwargs,     # repassados para o constructor do adapter
) -> StorageAdapter
```

---

## LocalAdapter

```python
from permafrost import LocalAdapter

adapter = LocalAdapter(base_dir="/tmp/storage/")

adapter.upload("local.permafrost", "/tmp/storage/arquivo.permafrost")
adapter.download("/tmp/storage/arquivo.permafrost", "local_copia.permafrost")
adapter.exists("/tmp/storage/arquivo.permafrost")     # bool
adapter.delete("/tmp/storage/arquivo.permafrost")     # bool
adapter.list("/tmp/storage/")                         # list[str]
adapter.read_header_bytes("/tmp/storage/arq.permafrost", n_bytes=4096)
adapter.read_footer_bytes("/tmp/storage/arq.permafrost", n_bytes=8192)
adapter.write_bytes(data: bytes, "/tmp/storage/arq.permafrost")
```

---

## S3Adapter

```python
from permafrost import S3Adapter

s3 = S3Adapter(
    region="us-east-1",                  # opcional
    endpoint_url="http://localhost:9000", # MinIO/S3-compatible
    storage_class="GLACIER_DEEP_ARCHIVE", # padrão: STANDARD
)

# Todos os métodos do LocalAdapter +
s3.set_lifecycle(
    bucket="meu-bucket",
    prefix="cold/",
    transition_days=30,
    target_class="GLACIER_DEEP_ARCHIVE",
)
```

**Range requests automáticos:** `read_header_bytes()` e `read_footer_bytes()`
usam HTTP Range requests — não baixam o arquivo inteiro.

---

## GCSAdapter

```python
from permafrost import GCSAdapter

gcs = GCSAdapter(project="meu-projeto")
# Autenticação: GOOGLE_APPLICATION_CREDENTIALS ou ADC
```

---

## AzureAdapter

```python
from permafrost import AzureAdapter

azure = AzureAdapter(conn_str="DefaultEndpointsProtocol=https;...")
# ou
azure = AzureAdapter(account_name="...", account_key="...", tier="Cool")
```

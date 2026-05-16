# Contributing

Obrigado pelo interesse em contribuir com o Permafrost!

---

## Setup do ambiente

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
pip install -e '.[dev]'
```

---

## Rodando os testes

```bash
# Suite completa
pytest tests/ -v

# Testes específicos
pytest tests/test_freeze_thaw.py -v
pytest tests/test_catalog.py -v
pytest tests/test_cluster.py -v

# Com cobertura
pytest tests/ --cov=src/permafrost --cov-report=html
```

---

## Estrutura do projeto

```
src/permafrost/
    __init__.py          # Expõe a API pública
    codec.py             # freeze(), thaw(), audit(), preditores
    catalog.py           # PermafrostCatalog (DuckDB)
    chunk_mode.py        # freeze_stream(), freeze_file(), thaw_iter()
    cli.py               # CLI (typer + rich)
    cluster.py           # Master, Worker, Client
    schema_detector.py   # SchemaDetector, DataType
    storage.py           # Adapters de cloud storage
```

---

## Como adicionar um novo codec

1. Escolher o próximo `codec_id` disponível em `codec.py`
2. Implementar `_compress_bytes()` e `_decompress_bytes()` para o novo codec
3. Adicionar a constante `CODEC_MEUCODEC = 0x0N`
4. Exportar a constante em `__init__.py`
5. Adicionar ao mapa de nomes em `audit()`
6. Escrever testes em `tests/test_freeze_thaw.py`

---

## Como adicionar um novo StorageAdapter

1. Criar classe que herda de `StorageAdapter` em `storage.py`
2. Implementar todos os métodos abstratos (`upload`, `download`, `exists`, etc.)
3. Adicionar ao factory `storage_from_uri()` com o novo scheme URI
4. Exportar em `__init__.py`
5. Adicionar dependência opcional em `pyproject.toml`

---

## Processo de PR

1. Fork + branch descritiva (`feat/zpaq-codec`, `fix/timestamp-encoding`)
2. Código + testes (sem regressão nos 91 testes existentes)
3. `pytest tests/ -v` deve passar completamente
4. PR com descrição do que e por quê

---

## SDK Stability & Versioning Policy

Permafrost follows [Semantic Versioning 2.0.0](https://semver.org/).

### What counts as the public API

Everything exported from `permafrost.__all__` is public API:

- `freeze()`, `thaw()`, `audit()`, `freeze_to()`, `thaw_from()`, `audit_remote()`
- `freeze_file()`, `freeze_stream()`, `thaw_iter()`
- `PermafrostContext`, `PermafrostCatalog`
- `LocalAdapter`, `S3Adapter`, `GCSAdapter`, `AzureAdapter`
- `PermafrostMaster`, `PermafrostWorker`, `PermafrostClient`
- All constants: `CODEC_*`, `QUANT_*`, `PRED_*`, `MAGIC`, `EOF_MAGIC`
- `SchemaDetector`, `DataType`, `FieldKind`
- `KeyProvider`, `LocalKeyProvider`, `AWSKMSProvider`, `GCPKMSProvider`
- `SchemaEvolutionError`, `apply_schema_evolution`, `schema_diff`
- `CODEC_AUTO`, `DataProfile`, `auto_select`, `profile_dataframe`
- `AuthError`, `RBACManager`, `ClusterUser`, `generate_token`, `validate_token`

Names prefixed with `_` are internal and may change at any time.

### Version policy

| Change type | Version bump | Example |
|-------------|-------------|---------|
| New public function, parameter, or constant | MINOR (0.x) | Adding `freeze_async()` |
| Breaking change to existing public API | MAJOR (x.0.0) | Renaming `thaw()` parameter |
| Bug fix that doesn't break callers | PATCH (0.0.x) | Fixing encoding edge case |
| Deprecation added (not removed) | MINOR | Adding `DeprecationWarning` |
| Deprecated symbol removed | MAJOR | Removing deprecated function |

### Deprecation process

Before removing or changing a public symbol:

1. Add a `DeprecationWarning` in the current MINOR release:

```python
import warnings

def old_name(*args, **kwargs):
    warnings.warn(
        "old_name() is deprecated since 0.8.0 and will be removed in 1.0.0. "
        "Use new_name() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return new_name(*args, **kwargs)
```

2. Document in changelog under a `### Deprecated` heading.
3. Keep the alias for at least **one MINOR version** before removal.
4. Removal happens in the next MAJOR release only.

### .permafrost file format compatibility

- Format version is independent of SDK version.
- Files written by v1.x are always readable by implementations supporting v1.y (y ≥ x).
- Breaking format changes require incrementing `VERSION_MAJOR` (byte 4 of file).
- The reference implementation in `codec.py` is the canonical source of truth.

---

## Code of Conduct

Este projeto segue o [Contributor Covenant](https://www.contributor-covenant.org/).
Seja respeitoso, construtivo e inclusivo.

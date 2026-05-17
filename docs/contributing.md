# Contributing

Thank you for your interest in contributing to Permafrost!

---

## Environment setup

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
pip install -e '.[dev]'
```

---

## Running tests

```bash
# Full suite
pytest tests/ -v

# Specific test files
pytest tests/test_freeze_thaw.py -v
pytest tests/test_catalog.py -v
pytest tests/test_cluster.py -v

# With coverage
pytest tests/ --cov=src/permafrost --cov-report=html
```

---

## Project structure

```
src/permafrost/
    __init__.py          # Public API exports
    codec.py             # freeze(), thaw(), audit(), predictors
    catalog.py           # PermafrostCatalog (DuckDB)
    chunk_mode.py        # freeze_stream(), freeze_file(), peek()
    cli.py               # CLI (typer + rich)
    cluster.py           # Master, Worker, Client
    schema_detector.py   # SchemaDetector, DataType
    storage.py           # Cloud storage adapters
    crypto.py            # AES-256-GCM encryption, KMS providers
    context.py           # PermafrostContext — unified high-level API
```

---

## How to add a new codec

1. Pick the next available `codec_id` in `codec.py`
2. Implement `_compress_bytes()` and `_decompress_bytes()` for the new codec
3. Add the constant `CODEC_MYCODEC = 0x0N`
4. Export the constant in `__init__.py`
5. Add to the name map in `audit()`
6. Write tests in `tests/test_freeze_thaw.py`

---

## How to add a new StorageAdapter

1. Create a class inheriting from `StorageAdapter` in `storage.py`
2. Implement all abstract methods (`upload`, `download`, `exists`, etc.)
3. Add to the `storage_from_uri()` factory with the new URI scheme
4. Export in `__init__.py`
5. Add optional dependency in `pyproject.toml`

---

## PR process

1. Fork + descriptive branch (`feat/zpaq-codec`, `fix/timestamp-encoding`)
2. Code + tests (no regression on the existing test suite)
3. `pytest tests/ -v` must pass completely
4. PR with description of what and why

---

## SDK Stability & Versioning Policy

Permafrost follows [Semantic Versioning 2.0.0](https://semver.org/).

### What counts as the public API

Everything exported from `permafrost.__all__` is public API:

- `freeze()`, `thaw()`, `audit()`, `freeze_to()`, `thaw_from()`, `audit_remote()`
- `freeze_file()`, `freeze_stream()`, `peek()`
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

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/).
Be respectful, constructive, and inclusive.

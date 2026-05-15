# API Reference — PermafrostCatalog

```python
cat = permafrost.PermafrostCatalog(
    catalog_path: str = ".permafrost_catalog.db"
    # Use ":memory:" para testes
)
```

---

## Registro

```python
cat.register(path: str, tags: list = None, name: str = None) -> dict
cat.register_dir(directory: str, tags: list = None, recursive: bool = False) -> list
```

---

## Busca

```python
cat.search(
    name: str = None,
    codec: str = None,
    partition_col: str = None,
    partition_key: str = None,
    columns_contain: str = None,
    min_rows: int = None,
    max_mb: float = None,
    tags_contain: str = None,
    lossless_only: bool = False,
) -> pd.DataFrame

cat.search_chunks(dataset_name: str, part_key: str = None) -> pd.DataFrame
```

---

## Thaw via catalog

```python
cat.thaw(
    name: str,
    filter: dict = None,
    row_range: tuple = None,
    verify: bool = True,
) -> pd.DataFrame
```

---

## Custo e integridade

```python
cat.cost_report(tier: str = "glacier_deep") -> pd.DataFrame
# tier: "s3_standard" | "s3_ia" | "glacier" | "glacier_deep"

cat.integrity_check(name_filter: str = None) -> pd.DataFrame
```

---

## Métricas

```python
cat.stats() -> dict
# {
#   "total_datasets": 4,
#   "total_rows": 540000,
#   "total_mb": 2.964,
#   "total_chunks": 54,
#   "lossless_count": 3,
#   "vault_count": 1,
# }
```

---

## SQL direto

```python
cat.sql(query: str) -> pd.DataFrame
# DuckDB SQL sobre as tabelas "datasets" e "chunks"
```

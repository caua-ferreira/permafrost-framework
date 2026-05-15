"""
Schema evolution for .permafrost files.

Lets data frozen with an old schema be read with a newer schema:
  - New columns    → null/NaN filled
  - Removed columns → dropped silently
  - Compatible type change → auto-cast (e.g. int32 → int64, float32 → float64)
  - Incompatible type change → SchemaEvolutionError with clear message
"""
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd


class SchemaEvolutionError(Exception):
    """Raised when a column cannot be cast to the requested target type."""


# ── Type helpers ──────────────────────────────────────────────────────────────

def _null_column(n_rows: int, pa_type, name: str) -> pd.Series:
    """Null-filled Series for a column that doesn't exist in the file."""
    import pyarrow as pa
    if pa.types.is_floating(pa_type):
        return pd.Series(np.full(n_rows, np.nan), dtype=np.float64, name=name)
    if pa.types.is_integer(pa_type):
        return pd.Series(np.zeros(n_rows, dtype=np.int64), name=name)
    if pa.types.is_boolean(pa_type):
        return pd.Series([None] * n_rows, dtype=object, name=name)
    if pa.types.is_timestamp(pa_type):
        return pd.Series(
            pd.array([pd.NaT] * n_rows, dtype='datetime64[ns]'), name=name
        )
    return pd.Series([None] * n_rows, dtype=object, name=name)


def _cast_column(series: pd.Series, pa_type, col_name: str) -> pd.Series:
    """Casts a Series to a PyArrow target type.

    Raises:
        SchemaEvolutionError: if cast is not possible.
    """
    import pyarrow as pa

    # Fast path: already the right type
    try:
        arr = pa.Array.from_pandas(series)
        if arr.type == pa_type:
            return series
    except Exception:
        pass

    # PyArrow cast (safe=False allows numeric down-casts)
    try:
        arr = pa.Array.from_pandas(series)
        casted = arr.cast(pa_type, safe=False)
        result = casted.to_pandas()
        result.name = col_name
        return result
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
        pass

    # pandas fallback
    try:
        pd_dtype = pa_type.to_pandas_dtype()
        return series.astype(pd_dtype)
    except Exception as exc:
        raise SchemaEvolutionError(
            f"Cannot evolve column '{col_name}': "
            f"stored dtype '{series.dtype}' → target '{pa_type}'. "
            f"Use a compatible type or add the column to schema as nullable."
        ) from exc


def _types_equivalent(pandas_dtype: str, pa_type_str: str) -> bool:
    """Loose equivalence between a pandas dtype string and a pyarrow type string."""
    _map = {
        'int64':   ('int64',),
        'int32':   ('int32',),
        'float64': ('double', 'float64'),
        'float32': ('float', 'float32'),
        'object':  ('string', 'large_string', 'utf8', 'object'),
        'bool':    ('bool',),
    }
    pd_low = pandas_dtype.lower()
    pa_low = pa_type_str.lower()
    for pd_t, pa_ts in _map.items():
        if pd_t in pd_low and any(p in pa_low for p in pa_ts):
            return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def apply_schema_evolution(df: pd.DataFrame, schema_override) -> pd.DataFrame:
    """Applies schema evolution rules to a thawed DataFrame.

    Column resolution rules (in order):
    1. In file + in schema → cast to target type
    2. In file, NOT in schema → dropped from result
    3. NOT in file, in schema → null-filled (NaN for numbers, None for strings)
    4. Cast failure → :exc:`SchemaEvolutionError`

    Args:
        df: DataFrame as returned by ``thaw()``.
        schema_override: ``pyarrow.Schema`` describing the desired output layout.

    Returns:
        New ``pd.DataFrame`` with columns in the order defined by ``schema_override``.

    Raises:
        SchemaEvolutionError: If a stored column cannot be cast to the target type.
        TypeError: If ``schema_override`` is not a ``pyarrow.Schema``.

    Example::

        import pyarrow as pa
        new_schema = pa.schema([
            pa.field("id",       pa.int64()),
            pa.field("price",    pa.float32()),   # was float64
            pa.field("category", pa.string()),    # new column → None-filled
            # "old_col" not listed → dropped
        ])
        df = pf.thaw("data.permafrost", schema_override=new_schema)
    """
    import pyarrow as pa
    if not isinstance(schema_override, pa.Schema):
        raise TypeError(
            f"schema_override must be a pyarrow.Schema, got {type(schema_override)}"
        )

    n = len(df)
    result: dict[str, pd.Series] = {}

    for i in range(len(schema_override)):
        field = schema_override.field(i)
        col = field.name
        if col in df.columns:
            result[col] = _cast_column(df[col], field.type, col)
        else:
            result[col] = _null_column(n, field.type, col)

    return pd.DataFrame(result)


def schema_diff(path: str, target_schema) -> dict[str, Any]:
    """Computes the diff between a file's stored schema and a target schema.

    Reads only the header — no decompression needed.

    Args:
        path: Path to the ``.permafrost`` file.
        target_schema: ``pyarrow.Schema`` to compare against.

    Returns:
        Dict with four keys:

        - ``added``: columns in target but NOT in file — will be null-filled on thaw
        - ``removed``: columns in file but NOT in target — will be dropped on thaw
        - ``type_changed``: list of ``(name, stored_dtype, target_type)`` — will be cast
        - ``unchanged``: columns with matching types in both

    Example::

        import pyarrow as pa
        diff = pf.schema_diff("archive.permafrost", pa.schema([
            pa.field("id",    pa.int64()),
            pa.field("price", pa.float32()),
            pa.field("tags",  pa.string()),
        ]))
        print(diff["added"])       # ['tags']
        print(diff["type_changed"])  # [('price', 'float64', 'float')]
    """
    import pyarrow as pa
    from permafrost.codec import _read_header

    if not isinstance(target_schema, pa.Schema):
        raise TypeError(
            f"target_schema must be a pyarrow.Schema, got {type(target_schema)}"
        )

    with open(path, 'rb') as f:
        raw = f.read(131072)
    h = _read_header(raw)

    stored_cols  = list(h['manifests'].keys())
    stored_dtype = {col: m.get('dtype', 'object') for col, m in h['manifests'].items()}
    target_map   = {
        target_schema.field(i).name: target_schema.field(i).type
        for i in range(len(target_schema))
    }

    added   = [c for c in target_map if c not in stored_dtype]
    removed = [c for c in stored_cols if c not in target_map]

    type_changed: list[tuple[str, str, str]] = []
    unchanged: list[str] = []

    for col in stored_cols:
        if col not in target_map:
            continue
        stored_dt = stored_dtype[col]
        target_dt = str(target_map[col])
        if stored_dt != target_dt and not _types_equivalent(stored_dt, target_dt):
            type_changed.append((col, stored_dt, target_dt))
        else:
            unchanged.append(col)

    return {
        'added':        added,
        'removed':      removed,
        'type_changed': type_changed,
        'unchanged':    unchanged,
    }

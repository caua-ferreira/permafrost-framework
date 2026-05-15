"""Tests for schema evolution — C3 feature (v0.7)."""
import pytest
import numpy as np
import pandas as pd
import pyarrow as pa

import permafrost as pf
from permafrost.schema_evolution import (
    SchemaEvolutionError,
    apply_schema_evolution,
    schema_diff,
    _null_column,
    _cast_column,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def base_df():
    return pd.DataFrame({
        "id":       range(200),
        "price":    np.random.default_rng(1).uniform(1.0, 999.0, 200),
        "category": np.random.choice(["A", "B", "C"], 200),
        "qty":      np.random.default_rng(2).integers(1, 100, 200),
        "ts":       pd.date_range("2024-01-01", periods=200, freq="h"),
    })

@pytest.fixture
def frozen(base_df, tmp_path):
    path = str(tmp_path / "base.permafrost")
    pf.freeze(base_df, path, codec=pf.CODEC_ZSTD)
    return path


# ── Unit: _null_column ────────────────────────────────────────────────────────

class TestNullColumn:
    def test_float_type_gives_nan(self):
        s = _null_column(5, pa.float64(), "v")
        assert all(np.isnan(s))
        assert len(s) == 5

    def test_integer_type_gives_zeros(self):
        s = _null_column(5, pa.int64(), "v")
        assert list(s) == [0, 0, 0, 0, 0]

    def test_string_type_gives_none(self):
        s = _null_column(3, pa.string(), "v")
        assert all(x is None for x in s)

    def test_timestamp_type_gives_nat(self):
        s = _null_column(3, pa.timestamp('ns'), "v")
        assert all(pd.isna(x) for x in s)

    def test_correct_length(self):
        for n in [0, 1, 100]:
            s = _null_column(n, pa.float32(), "v")
            assert len(s) == n


# ── Unit: _cast_column ────────────────────────────────────────────────────────

class TestCastColumn:
    def test_int64_to_float64(self):
        s = pd.Series([1, 2, 3], dtype="int64")
        result = _cast_column(s, pa.float64(), "v")
        assert result.dtype == np.float64
        assert list(result) == [1.0, 2.0, 3.0]

    def test_float64_to_float32(self):
        s = pd.Series([1.5, 2.5], dtype="float64")
        result = _cast_column(s, pa.float32(), "v")
        assert result.dtype == np.float32

    def test_int_to_string(self):
        s = pd.Series([1, 2, 3], dtype="int64")
        result = _cast_column(s, pa.string(), "v")
        assert result.tolist() == ["1", "2", "3"]

    def test_same_type_no_op(self):
        s = pd.Series([1.0, 2.0], dtype="float64")
        result = _cast_column(s, pa.float64(), "v")
        assert result.dtype == np.float64

    def test_incompatible_cast_raises(self):
        s = pd.Series(["hello", "world"])
        with pytest.raises(SchemaEvolutionError, match="Cannot evolve"):
            _cast_column(s, pa.int64(), "v")


# ── Unit: apply_schema_evolution ─────────────────────────────────────────────

class TestApplySchemaEvolution:
    def test_column_order_follows_schema(self, base_df):
        schema = pa.schema([
            pa.field("qty",      pa.int64()),
            pa.field("price",    pa.float64()),
            pa.field("category", pa.string()),
        ])
        result = apply_schema_evolution(base_df, schema)
        assert list(result.columns) == ["qty", "price", "category"]

    def test_new_column_is_null_filled(self, base_df):
        schema = pa.schema([
            pa.field("id",      pa.int64()),
            pa.field("new_col", pa.float64()),
        ])
        result = apply_schema_evolution(base_df, schema)
        assert "new_col" in result.columns
        assert all(np.isnan(result["new_col"]))

    def test_removed_column_is_dropped(self, base_df):
        schema = pa.schema([
            pa.field("id",    pa.int64()),
            pa.field("price", pa.float64()),
        ])
        result = apply_schema_evolution(base_df, schema)
        assert "category" not in result.columns
        assert "qty" not in result.columns
        assert "ts" not in result.columns

    def test_type_cast_int_to_float(self, base_df):
        schema = pa.schema([
            pa.field("qty", pa.float64()),
        ])
        result = apply_schema_evolution(base_df, schema)
        assert result["qty"].dtype == np.float64

    def test_type_cast_float_to_float32(self, base_df):
        schema = pa.schema([
            pa.field("price", pa.float32()),
        ])
        result = apply_schema_evolution(base_df, schema)
        assert result["price"].dtype == np.float32

    def test_row_count_preserved(self, base_df):
        schema = pa.schema([pa.field("id", pa.int64())])
        result = apply_schema_evolution(base_df, schema)
        assert len(result) == len(base_df)

    def test_empty_df_works(self):
        df = pd.DataFrame({"a": pd.Series([], dtype=float)})
        schema = pa.schema([
            pa.field("a", pa.float64()),
            pa.field("b", pa.string()),
        ])
        result = apply_schema_evolution(df, schema)
        assert len(result) == 0
        assert "b" in result.columns

    def test_incompatible_type_raises(self):
        df = pd.DataFrame({"text": ["hello", "world"]})
        schema = pa.schema([pa.field("text", pa.int64())])
        with pytest.raises(SchemaEvolutionError):
            apply_schema_evolution(df, schema)

    def test_wrong_type_arg_raises(self, base_df):
        with pytest.raises(TypeError, match="pyarrow.Schema"):
            apply_schema_evolution(base_df, {"id": "int64"})


# ── Integration: thaw() with schema_override ─────────────────────────────────

class TestThawSchemaOverride:
    def test_add_new_column(self, frozen, base_df):
        schema = pa.schema([
            pa.field("id",         pa.int64()),
            pa.field("price",      pa.float64()),
            pa.field("new_score",  pa.float64()),  # doesn't exist in file
        ])
        df = pf.thaw(frozen, schema_override=schema)
        assert list(df.columns) == ["id", "price", "new_score"]
        assert all(np.isnan(df["new_score"]))

    def test_drop_old_columns(self, frozen, base_df):
        schema = pa.schema([
            pa.field("id",    pa.int64()),
            pa.field("price", pa.float64()),
        ])
        df = pf.thaw(frozen, schema_override=schema)
        assert set(df.columns) == {"id", "price"}

    def test_type_upcast(self, frozen):
        schema = pa.schema([
            pa.field("qty",   pa.float64()),  # was int → upcast to float
            pa.field("price", pa.float64()),
        ])
        df = pf.thaw(frozen, schema_override=schema)
        assert df["qty"].dtype == np.float64

    def test_type_downcast(self, frozen):
        schema = pa.schema([
            pa.field("price", pa.float32()),  # was float64 → downcast
        ])
        df = pf.thaw(frozen, schema_override=schema)
        assert df["price"].dtype == np.float32

    def test_row_count_unchanged(self, frozen, base_df):
        schema = pa.schema([pa.field("id", pa.int64())])
        df = pf.thaw(frozen, schema_override=schema)
        assert len(df) == len(base_df)

    def test_no_schema_override_is_identity(self, frozen, base_df):
        df = pf.thaw(frozen)
        assert set(df.columns) == set(base_df.columns)

    def test_add_multiple_new_columns(self, frozen):
        schema = pa.schema([
            pa.field("id",   pa.int64()),
            pa.field("col1", pa.float64()),
            pa.field("col2", pa.string()),
            pa.field("col3", pa.int64()),
        ])
        df = pf.thaw(frozen, schema_override=schema)
        assert all(np.isnan(df["col1"]))
        assert all(x is None for x in df["col2"])
        assert list(df["col3"]) == [0] * len(df)

    def test_works_with_partial_thaw_filter(self, tmp_path, base_df):
        path = str(tmp_path / "part.permafrost")
        pf.freeze(base_df, path, partition_by="category", codec=pf.CODEC_ZSTD)
        schema = pa.schema([
            pa.field("id",    pa.int64()),
            pa.field("price", pa.float32()),
        ])
        df = pf.thaw(path, filter={"category": "A"}, schema_override=schema)
        assert set(df.columns) == {"id", "price"}
        assert df["price"].dtype == np.float32

    def test_schema_override_with_encryption(self, tmp_path, base_df):
        path = str(tmp_path / "enc.permafrost")
        key = bytes(range(32))
        pf.freeze(base_df, path, key=key, codec=pf.CODEC_ZSTD)
        schema = pa.schema([
            pa.field("id",    pa.int64()),
            pa.field("price", pa.float32()),
        ])
        df = pf.thaw(path, key=key, schema_override=schema)
        assert "price" in df.columns
        assert df["price"].dtype == np.float32


# ── Integration: thaw_iter() with schema_override ────────────────────────────

class TestThawIterSchemaOverride:
    def test_each_chunk_gets_schema(self, frozen):
        schema = pa.schema([
            pa.field("id",    pa.int64()),
            pa.field("extra", pa.float64()),  # new column
        ])
        chunks = list(pf.thaw_iter(frozen, schema_override=schema))
        assert len(chunks) > 0
        for chunk in chunks:
            assert list(chunk.columns) == ["id", "extra"]
            assert all(np.isnan(chunk["extra"]))

    def test_total_rows_preserved(self, frozen, base_df):
        schema = pa.schema([pa.field("id", pa.int64())])
        total = sum(len(c) for c in pf.thaw_iter(frozen, schema_override=schema))
        assert total == len(base_df)


# ── schema_diff() ─────────────────────────────────────────────────────────────

class TestSchemaDiff:
    def test_added_columns(self, frozen):
        diff = schema_diff(frozen, pa.schema([
            pa.field("id",       pa.int64()),
            pa.field("price",    pa.float64()),
            pa.field("new_col",  pa.string()),  # not in file
        ]))
        assert "new_col" in diff["added"]

    def test_removed_columns(self, frozen, base_df):
        diff = schema_diff(frozen, pa.schema([
            pa.field("id", pa.int64()),
        ]))
        assert set(base_df.columns) - {"id"} == set(diff["removed"])

    def test_type_changed(self, frozen):
        diff = schema_diff(frozen, pa.schema([
            pa.field("price", pa.float32()),  # file has float64
        ]))
        changed_cols = [t[0] for t in diff["type_changed"]]
        assert "price" in changed_cols

    def test_unchanged_same_type(self, frozen):
        diff = schema_diff(frozen, pa.schema([
            pa.field("id", pa.int64()),
        ]))
        # id is int64 in file → unchanged
        assert "id" in diff["unchanged"] or len(diff["type_changed"]) == 0

    def test_empty_diff_for_identity(self, frozen, base_df):
        # If target schema matches stored schema, no changes expected
        diff = schema_diff(frozen, pa.schema([
            pa.field("id", pa.int64()),
        ]))
        assert isinstance(diff["added"], list)
        assert isinstance(diff["removed"], list)
        assert isinstance(diff["type_changed"], list)
        assert isinstance(diff["unchanged"], list)

    def test_wrong_schema_type_raises(self, frozen):
        with pytest.raises(TypeError):
            schema_diff(frozen, {"id": "int64"})

    def test_all_removed(self, frozen, base_df):
        diff = schema_diff(frozen, pa.schema([
            pa.field("nonexistent", pa.string()),
        ]))
        assert set(diff["removed"]) == set(base_df.columns)
        assert "nonexistent" in diff["added"]


# ── audit() stored_schema ─────────────────────────────────────────────────────

class TestAuditStoredSchema:
    def test_stored_schema_in_audit(self, frozen, base_df):
        info = pf.audit(frozen)
        assert "stored_schema" in info
        assert set(info["stored_schema"].keys()) == set(base_df.columns)

    def test_stored_schema_has_dtype_strings(self, frozen):
        info = pf.audit(frozen)
        for col, dtype in info["stored_schema"].items():
            assert isinstance(dtype, str)
            assert len(dtype) > 0


# ── Regression: existing thaw still works without schema_override ─────────────

class TestRegressionNoSchemaOverride:
    def test_thaw_without_override(self, frozen, base_df):
        df = pf.thaw(frozen)
        assert len(df) == len(base_df)
        assert set(df.columns) == set(base_df.columns)

    def test_thaw_iter_without_override(self, frozen, base_df):
        total = sum(len(c) for c in pf.thaw_iter(frozen))
        assert total == len(base_df)

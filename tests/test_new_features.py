"""Tests for freeze_append, range filter and Polars integration."""
import os
import pytest
import numpy as np
import pandas as pd
import permafrost as pf


# ── helpers ───────────────────────────────────────────────────────────────────
def _make_df(years, n_per_year=500, seed=0):
    rng = np.random.default_rng(seed)
    rows = n_per_year * len(years)
    anos = np.repeat(years, n_per_year)
    return pd.DataFrame({
        "id":    np.arange(rows, dtype=np.int64),
        "ano":   anos.astype(np.int16),
        "mes":   rng.integers(1, 13, rows).astype(np.int8),
        "valor": np.round(rng.uniform(10, 9999, rows), 2),
        "cat":   pd.Categorical(rng.choice(["A","B","C"], rows)),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 1 — freeze_append
# ═══════════════════════════════════════════════════════════════════════════════
class TestFreezeAppend:

    def test_basic_append_roundtrip(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df1 = _make_df([2022, 2023])
        df2 = _make_df([2024], seed=1)

        pf.freeze(df1, p, codec=pf.CODEC_ZSTD, partition_by="ano")
        result = pf.freeze_append(p, df2)

        assert result["appended_rows"] == len(df2)
        assert result["total_rows"] == len(df1) + len(df2)

        full = pf.unfreeze(p)
        assert len(full) == len(df1) + len(df2)
        assert set(full["ano"].unique()) == {2022, 2023, 2024}

    def test_append_preserves_existing_data(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df1 = _make_df([2022])
        df2 = _make_df([2023], seed=99)

        pf.freeze(df1, p, codec=pf.CODEC_ZSTD)
        pf.freeze_append(p, df2)

        full = pf.unfreeze(p)
        pd.testing.assert_frame_equal(
            full.iloc[:len(df1)].reset_index(drop=True),
            df1.reset_index(drop=True),
            check_dtype=False,
        )

    def test_append_sparse_index_filter_works(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df1 = _make_df([2020, 2021, 2022])
        df2 = _make_df([2023, 2024], seed=2)

        pf.freeze(df1, p, codec=pf.CODEC_ZSTD, partition_by="ano")
        pf.freeze_append(p, df2)

        # filter on year that's in the appended batch
        df_2024 = pf.unfreeze(p, filter={"ano": 2024})
        assert len(df_2024) > 0
        assert (df_2024["ano"] == 2024).all()

        # filter on year from original batch
        df_2021 = pf.unfreeze(p, filter={"ano": 2021})
        assert len(df_2021) > 0
        assert (df_2021["ano"] == 2021).all()

    def test_append_schema_mismatch_raises(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df1 = _make_df([2022])
        df_bad = df1.drop(columns=["cat"])

        pf.freeze(df1, p, codec=pf.CODEC_ZSTD)
        with pytest.raises(ValueError, match="Schema mismatch"):
            pf.freeze_append(p, df_bad)

    def test_append_audit_reflects_new_totals(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df1 = _make_df([2022])
        df2 = _make_df([2023], seed=5)

        pf.freeze(df1, p, codec=pf.CODEC_ZSTD)
        pf.freeze_append(p, df2)

        info = pf.audit(p)
        assert info["orig_rows"] == len(df1) + len(df2)

    def test_multiple_appends(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df_base = _make_df([2020])
        pf.freeze(df_base, p, codec=pf.CODEC_ZSTD, partition_by="ano")

        for year, seed in [(2021,1),(2022,2),(2023,3)]:
            pf.freeze_append(p, _make_df([year], seed=seed))

        full = pf.unfreeze(p)
        assert len(full) == 500 * 4
        assert set(full["ano"].unique()) == {2020, 2021, 2022, 2023}


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 2 — Range filter
# ═══════════════════════════════════════════════════════════════════════════════
class TestRangeFilter:

    def test_range_returns_subset(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df = _make_df([2020, 2021, 2022, 2023, 2024])
        pf.freeze(df.sort_values("ano"), p, codec=pf.CODEC_ZSTD, partition_by="ano")

        result = pf.unfreeze(p, filter={"ano": (2021, 2022)})
        assert set(result["ano"].unique()).issubset({2021, 2022})
        assert len(result) > 0

    def test_range_excludes_outside_years(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df = _make_df([2020, 2021, 2022, 2023, 2024])
        pf.freeze(df.sort_values("ano"), p, codec=pf.CODEC_ZSTD, partition_by="ano")

        result = pf.unfreeze(p, filter={"ano": (2021, 2022)})
        assert 2020 not in result["ano"].values
        assert 2023 not in result["ano"].values
        assert 2024 not in result["ano"].values

    def test_range_single_year_matches_exact(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df = _make_df([2020, 2021, 2022])
        pf.freeze(df.sort_values("ano"), p, codec=pf.CODEC_ZSTD, partition_by="ano")

        result_range = pf.unfreeze(p, filter={"ano": (2022, 2022)})
        result_exact = pf.unfreeze(p, filter={"ano": 2022})
        assert len(result_range) == len(result_exact)

    def test_range_full_span_returns_all(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df = _make_df([2020, 2021, 2022])
        pf.freeze(df.sort_values("ano"), p, codec=pf.CODEC_ZSTD, partition_by="ano")

        result = pf.unfreeze(p, filter={"ano": (2020, 2022)})
        assert len(result) == len(df)

    def test_range_empty_returns_empty(self, tmp_path):
        p = str(tmp_path / "data.permafrost")
        df = _make_df([2020, 2021, 2022])
        pf.freeze(df.sort_values("ano"), p, codec=pf.CODEC_ZSTD, partition_by="ano")

        result = pf.unfreeze(p, filter={"ano": (2030, 2035)})
        assert len(result) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 3 — Polars integration
# ═══════════════════════════════════════════════════════════════════════════════
polars_available = pytest.mark.skipif(
    __import__("importlib").util.find_spec("polars") is None,
    reason="polars not installed",
)


class TestPolarsIntegration:

    @polars_available
    def test_freeze_polars_roundtrip(self, tmp_path):
        import polars
        p = str(tmp_path / "data.permafrost")
        df_pd = _make_df([2022, 2023])
        df_pl = polars.from_pandas(df_pd)

        pf.freeze(df_pl, p, codec=pf.CODEC_ZSTD, partition_by="ano")
        result = pf.unfreeze(p)

        assert len(result) == len(df_pd)
        assert list(result.columns) == list(df_pd.columns)

    @polars_available
    def test_thaw_engine_polars(self, tmp_path):
        import polars
        p = str(tmp_path / "data.permafrost")
        df = _make_df([2022])
        pf.freeze(df, p, codec=pf.CODEC_ZSTD)

        result = pf.unfreeze(p, engine='polars')
        assert isinstance(result, polars.DataFrame)
        assert len(result) == len(df)

    @polars_available
    def test_thaw_polars_with_filter(self, tmp_path):
        import polars
        p = str(tmp_path / "data.permafrost")
        df = _make_df([2022, 2023, 2024])
        pf.freeze(df.sort_values("ano"), p, codec=pf.CODEC_ZSTD, partition_by="ano")

        result = pf.unfreeze(p, filter={"ano": 2023}, engine='polars')
        assert isinstance(result, polars.DataFrame)
        assert (result["ano"] == 2023).all()

    @polars_available
    def test_freeze_polars_data_fidelity(self, tmp_path):
        import polars
        p = str(tmp_path / "data.permafrost")
        df_pd = _make_df([2022])
        df_pl = polars.from_pandas(df_pd)

        pf.freeze(df_pl, p, codec=pf.CODEC_ZSTD)
        result_pd = pf.unfreeze(p)

        pd.testing.assert_frame_equal(
            result_pd.reset_index(drop=True),
            df_pd.reset_index(drop=True),
            check_dtype=False,
        )

"""Tests for I2 — Codec Auto-Selector."""
import pytest
import numpy as np
import pandas as pd

import permafrost as pf
from permafrost.auto_codec import (
    CODEC_AUTO,
    DataProfile,
    profile_dataframe,
    auto_select,
)
from permafrost.codec import CODEC_ZSTD, CODEC_LZMA2, QUANT_NONE, QUANT_HIGH


# ── profile_dataframe ─────────────────────────────────────────────────────────

class TestProfileDataframe:
    def test_empty_df_zero_cols(self):
        p = profile_dataframe(pd.DataFrame())
        assert p.n_cols == 0

    def test_zero_rows_handled(self):
        df = pd.DataFrame({"a": pd.Series([], dtype=float)})
        p = profile_dataframe(df, sample_size=100)
        assert p.n_rows == 0

    def test_all_float_cols(self):
        df = pd.DataFrame({"a": np.random.rand(100), "b": np.random.rand(100)})
        p = profile_dataframe(df)
        assert p.float_col_ratio == 1.0
        assert p.int_col_ratio == 0.0
        assert p.str_col_ratio == 0.0
        assert p.ts_col_ratio == 0.0

    def test_all_int_cols(self):
        df = pd.DataFrame({"x": range(100), "y": range(100, 200)})
        p = profile_dataframe(df)
        assert p.int_col_ratio == 1.0
        assert p.float_col_ratio == 0.0

    def test_all_ts_cols(self):
        df = pd.DataFrame({"t": pd.date_range("2024", periods=100, freq="h")})
        p = profile_dataframe(df)
        assert p.ts_col_ratio == 1.0

    def test_mixed_types_sum_to_one(self):
        df = pd.DataFrame({
            "id":    range(200),
            "price": np.random.rand(200),
            "cat":   ["A", "B"] * 100,
            "ts":    pd.date_range("2024", periods=200, freq="min"),
        })
        p = profile_dataframe(df)
        total = p.float_col_ratio + p.int_col_ratio + p.str_col_ratio + p.ts_col_ratio
        assert abs(total - 1.0) < 1e-9

    def test_low_cardinality_str_detected(self):
        df = pd.DataFrame({"cat": (["A", "B", "C"] * 200)[:300]})
        p = profile_dataframe(df, sample_size=300)
        assert p.str_cardinality_mean < 0.05

    def test_high_cardinality_str_detected(self):
        df = pd.DataFrame({"name": [f"user_{i}" for i in range(500)]})
        p = profile_dataframe(df, sample_size=500)
        assert p.str_cardinality_mean > 0.9

    def test_sample_size_limits_n_rows(self):
        df = pd.DataFrame({"x": range(10_000)})
        p = profile_dataframe(df, sample_size=200)
        assert p.n_rows == 200

    def test_float_cv_near_zero_for_constant(self):
        df = pd.DataFrame({"a": [5.0] * 200})
        p = profile_dataframe(df)
        assert p.float_cv_mean < 0.01

    def test_float_cv_high_for_random(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({"a": rng.uniform(0, 100, 1000)})
        p = profile_dataframe(df)
        assert p.float_cv_mean > 0.5

    def test_estimated_mb_positive(self):
        df = pd.DataFrame({"a": np.random.rand(1000)})
        p = profile_dataframe(df)
        assert p.estimated_mb > 0

    def test_returns_DataProfile_instance(self):
        df = pd.DataFrame({"x": range(50)})
        assert isinstance(profile_dataframe(df), DataProfile)


# ── auto_select ───────────────────────────────────────────────────────────────

class TestAutoSelect:
    def test_returns_required_keys(self):
        df = pd.DataFrame({"x": np.random.rand(100)})
        r = auto_select(df)
        assert {"codec", "quant", "reason", "profile"} <= r.keys()

    def test_codec_is_valid_int(self):
        df = pd.DataFrame({"x": np.random.rand(100)})
        assert auto_select(df)["codec"] in (CODEC_ZSTD, CODEC_LZMA2)

    def test_quant_is_valid_int(self):
        from permafrost.codec import QUANT_NONE, QUANT_HIGH, QUANT_MEDIUM, QUANT_LOW
        df = pd.DataFrame({"x": np.random.rand(100)})
        assert auto_select(df)["quant"] in (QUANT_NONE, QUANT_HIGH, QUANT_MEDIUM, QUANT_LOW)

    def test_reason_is_nonempty_string(self):
        df = pd.DataFrame({"x": range(50)})
        r = auto_select(df)
        assert isinstance(r["reason"], str) and len(r["reason"]) > 0

    def test_profile_is_DataProfile(self):
        df = pd.DataFrame({"x": range(50)})
        assert isinstance(auto_select(df)["profile"], DataProfile)

    def test_empty_df_returns_defaults(self):
        r = auto_select(pd.DataFrame())
        assert r["codec"] == CODEC_ZSTD
        assert r["quant"] == QUANT_NONE
        assert r["profile"] is None

    def test_wrong_type_raises_TypeError(self):
        with pytest.raises(TypeError, match="pd.DataFrame"):
            auto_select([1, 2, 3])

    # ── Heurística: LZMA2 para dados repetitivos ──────────────────────────────

    def test_low_cardinality_strings_select_lzma2(self):
        df = pd.DataFrame({
            "cat": (["A", "B", "C"] * 500)[:1000],
            "val": np.random.rand(1000),
        })
        assert auto_select(df)["codec"] == CODEC_LZMA2

    def test_timestamps_select_lzma2(self):
        df = pd.DataFrame({
            "ts":  pd.date_range("2024", periods=1000, freq="s"),
            "val": np.random.rand(1000),
        })
        assert auto_select(df)["codec"] == CODEC_LZMA2

    def test_many_integers_select_lzma2(self):
        df = pd.DataFrame({col: range(1000) for col in ["a", "b", "c", "d"]})
        assert auto_select(df)["codec"] == CODEC_LZMA2

    def test_low_variance_floats_select_lzma2(self):
        rng = np.random.default_rng(0)
        df = pd.DataFrame({
            "a": rng.normal(100, 0.1, 1000),
            "b": rng.normal(200, 0.2, 1000),
        })
        assert auto_select(df)["codec"] == CODEC_LZMA2

    # ── Heurística: ZSTD para dados de alta entropia ──────────────────────────

    def test_high_variance_floats_select_zstd(self):
        rng = np.random.default_rng(1)
        df = pd.DataFrame({
            "a": rng.standard_normal(1000) * 1000,
            "b": rng.standard_normal(1000) * 1000,
            "c": rng.standard_normal(1000) * 1000,
            "d": rng.standard_normal(1000) * 1000,
        })
        assert auto_select(df)["codec"] == CODEC_ZSTD

    # ── Quant sugerido apenas em casos específicos ────────────────────────────

    def test_quant_none_for_mixed_profile(self):
        df = pd.DataFrame({
            "id":  range(1000),
            "cat": (["X", "Y"] * 500),
            "val": np.random.rand(1000),
        })
        assert auto_select(df)["quant"] == QUANT_NONE


# ── Integração: freeze com codec="auto" ──────────────────────────────────────

class TestFreezeAutoCodec:
    def test_codec_string_auto_works(self, tmp_path):
        df = pd.DataFrame({"a": range(500), "b": np.random.rand(500)})
        path = str(tmp_path / "out.permafrost")
        m = pf.freeze(df, path, codec="auto")
        assert m["ratio"] > 1.0

    def test_CODEC_AUTO_constant_works(self, tmp_path):
        df = pd.DataFrame({"x": range(200)})
        path = str(tmp_path / "out.permafrost")
        m = pf.freeze(df, path, codec=pf.CODEC_AUTO)
        assert "ratio" in m

    def test_auto_roundtrip_correct(self, tmp_path):
        df = pd.DataFrame({
            "id":    range(300),
            "price": np.random.rand(300) * 100,
            "cat":   (["A", "B", "C"] * 100),
        })
        path = str(tmp_path / "out.permafrost")
        pf.freeze(df, path, codec="auto")
        df_back = pf.thaw(path)
        assert len(df_back) == len(df)
        assert set(df_back.columns) == set(df.columns)

    def test_auto_reason_in_metrics(self, tmp_path):
        df = pd.DataFrame({"x": range(100)})
        path = str(tmp_path / "out.permafrost")
        m = pf.freeze(df, path, codec="auto")
        assert "auto_reason" in m
        assert isinstance(m["auto_reason"], str)
        assert len(m["auto_reason"]) > 0

    def test_manual_codec_has_no_auto_reason(self, tmp_path):
        df = pd.DataFrame({"x": range(100)})
        path = str(tmp_path / "out.permafrost")
        m = pf.freeze(df, path, codec=pf.CODEC_ZSTD)
        assert "auto_reason" not in m

    def test_audit_reads_auto_file(self, tmp_path):
        df = pd.DataFrame({
            "ts":  pd.date_range("2024", periods=400, freq="h"),
            "cat": (["X", "Y", "Z"] * 200)[:400],
        })
        path = str(tmp_path / "out.permafrost")
        pf.freeze(df, path, codec="auto")
        info = pf.audit(path)
        assert info["orig_rows"] == 400

    def test_auto_with_encryption_works(self, tmp_path):
        df = pd.DataFrame({"a": range(100), "b": np.random.rand(100)})
        path = str(tmp_path / "enc.permafrost")
        key = bytes(range(32))
        pf.freeze(df, path, codec="auto", key=key)
        df_back = pf.thaw(path, key=key)
        assert len(df_back) == 100

    def test_auto_with_partition_works(self, tmp_path):
        df = pd.DataFrame({
            "cat": (["A", "B", "C"] * 200)[:500],
            "val": np.random.rand(500),
        })
        path = str(tmp_path / "part.permafrost")
        pf.freeze(df, path, codec="auto", partition_by="cat")
        df_back = pf.thaw(path, filter={"cat": "A"})
        assert len(df_back) > 0


# ── CODEC_AUTO exportado corretamente ─────────────────────────────────────────

class TestCODECAutoExport:
    def test_CODEC_AUTO_exported(self):
        assert hasattr(pf, "CODEC_AUTO")

    def test_CODEC_AUTO_is_string_auto(self):
        assert pf.CODEC_AUTO == "auto"

    def test_auto_select_exported(self):
        assert callable(pf.auto_select)

    def test_profile_dataframe_exported(self):
        assert callable(pf.profile_dataframe)

    def test_DataProfile_exported(self):
        assert pf.DataProfile is DataProfile

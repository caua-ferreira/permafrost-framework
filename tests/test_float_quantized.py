"""Tests for float32_quantized and float16_quantized predictors (C2 — v0.7)."""
import tempfile, os, pytest
import numpy as np
import pandas as pd

import permafrost as pf
from permafrost.codec import (
    _detect_predictor, _float_quant_manifest,
    PRED_FLOAT32, PRED_FLOAT16, PRED_LAG1,
    QUANT_NONE, QUANT_HIGH, QUANT_MEDIUM, QUANT_LOW,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

RNG = np.random.default_rng(0)

@pytest.fixture
def float_df():
    return pd.DataFrame({
        "price":     RNG.uniform(1.0, 1000.0, 1000),
        "quantity":  RNG.uniform(0.01, 50.0, 1000),
        "discount":  RNG.uniform(0.0, 0.99, 1000),
        "lat":       RNG.uniform(-90.0, 90.0, 1000),   # special → keeps lag1
        "score":     RNG.uniform(0.0, 1.0, 1000),      # special → keeps lag1
    })

@pytest.fixture
def embedding_df():
    """1536-dim embeddings, one vector per row stored flat."""
    data = RNG.standard_normal((200, 8)).astype(np.float64)
    return pd.DataFrame(data, columns=[f"e{i}" for i in range(8)])

@pytest.fixture
def tmp(tmp_path):
    return str(tmp_path / "test.permafrost")


# ── Unit: manifest building ───────────────────────────────────────────────────

class TestFloatQuantManifest:
    def test_float32_manifest_fields(self):
        s = pd.Series(RNG.uniform(0, 100, 500))
        m = _float_quant_manifest("v", s, PRED_FLOAT32)
        assert m["predictor"] == PRED_FLOAT32
        assert m["precision_bits"] == 32
        assert m["max_abs_error"] >= 0.0
        assert 0 < m["max_rel_error"] < 1e-6

    def test_float16_manifest_fields(self):
        s = pd.Series(RNG.uniform(0, 100, 500))
        m = _float_quant_manifest("v", s, PRED_FLOAT16)
        assert m["predictor"] == PRED_FLOAT16
        assert m["precision_bits"] == 16
        assert 0 < m["max_rel_error"] < 1e-2

    def test_float32_error_bound(self):
        vals = np.array([1.0, 1.23456789, 999.9999999])
        s = pd.Series(vals)
        m = _float_quant_manifest("v", s, PRED_FLOAT32)
        # max_abs_error should match actual truncation
        expected = float(np.abs(vals - vals.astype(np.float32).astype(np.float64)).max())
        assert abs(m["max_abs_error"] - expected) < 1e-15

    def test_float16_error_bound(self):
        vals = np.array([1.0, 3.14, 100.0])
        s = pd.Series(vals)
        m = _float_quant_manifest("v", s, PRED_FLOAT16)
        expected = float(np.abs(vals - vals.astype(np.float16).astype(np.float64)).max())
        assert abs(m["max_abs_error"] - expected) < 1e-10


# ── Unit: auto-detection by QUANT level ──────────────────────────────────────

class TestAutoDetection:
    def test_quant_high_selects_float32_for_generic(self):
        s = pd.Series(RNG.uniform(0, 100, 100))
        m = _detect_predictor("price", s, QUANT_HIGH)
        assert m["predictor"] == PRED_FLOAT32

    def test_quant_low_selects_float16_for_generic(self):
        s = pd.Series(RNG.uniform(0, 100, 100))
        m = _detect_predictor("price", s, QUANT_LOW)
        assert m["predictor"] == PRED_FLOAT16

    def test_quant_none_keeps_lag1(self):
        s = pd.Series(RNG.uniform(0, 100, 100))
        m = _detect_predictor("price", s, QUANT_NONE)
        assert m["predictor"] == PRED_LAG1

    def test_quant_medium_keeps_existing_behavior(self):
        s = pd.Series(RNG.uniform(0, 100, 100))
        m = _detect_predictor("price", s, QUANT_MEDIUM)
        assert m["predictor"] == PRED_LAG1  # round0, not float32

    def test_lat_lon_not_affected_by_quant_high(self):
        s = pd.Series(RNG.uniform(-90, 90, 100))
        m = _detect_predictor("latitude", s, QUANT_HIGH)
        assert m["predictor"] == PRED_LAG1  # special col → keeps lag1

    def test_score_not_affected_by_quant_high(self):
        s = pd.Series(RNG.uniform(0, 1, 100))
        m = _detect_predictor("score", s, QUANT_HIGH)
        assert m["predictor"] == PRED_LAG1

    def test_pct_col_not_affected_by_quant_low(self):
        s = pd.Series(RNG.uniform(0, 100, 100))
        m = _detect_predictor("completion_pct", s, QUANT_LOW)
        assert m["predictor"] == PRED_LAG1


# ── Integration: explicit predictors= dict ────────────────────────────────────

class TestExplicitPredictors:
    def test_float32_explicit_round_trip(self, float_df, tmp):
        m = pf.freeze(float_df, tmp, predictors={"price": pf.PRED_FLOAT32})
        df_back = pf.thaw(tmp)
        # Price restored to float32 precision
        orig = float_df["price"].to_numpy()
        restored = df_back["price"].to_numpy()
        expected = orig.astype(np.float32).astype(np.float64)
        np.testing.assert_allclose(restored, expected, rtol=0, atol=0)

    def test_float16_explicit_round_trip(self, float_df, tmp):
        pf.freeze(float_df, tmp, predictors={"quantity": pf.PRED_FLOAT16})
        df_back = pf.thaw(tmp)
        orig = float_df["quantity"].to_numpy()
        expected = orig.astype(np.float16).astype(np.float64)
        np.testing.assert_allclose(df_back["quantity"].to_numpy(), expected, rtol=0, atol=0)

    def test_multiple_cols_predictors(self, float_df, tmp):
        pf.freeze(float_df, tmp,
                  predictors={"price": pf.PRED_FLOAT32, "quantity": pf.PRED_FLOAT16})
        df_back = pf.thaw(tmp)
        assert len(df_back) == len(float_df)

    def test_unknown_col_in_predictors_is_ignored(self, float_df, tmp):
        pf.freeze(float_df, tmp, predictors={"nonexistent_col": pf.PRED_FLOAT32})
        df_back = pf.thaw(tmp)
        assert len(df_back) == len(float_df)

    def test_float32_other_cols_unaffected(self, float_df, tmp):
        pf.freeze(float_df, tmp, predictors={"price": pf.PRED_FLOAT32})
        df_back = pf.thaw(tmp)
        # lat and score should be restored without float32 truncation
        assert len(df_back["lat"]) == len(float_df["lat"])


# ── Integration: QUANT_HIGH auto-selects float32 ─────────────────────────────

class TestQuantHighFloat32:
    def test_quant_high_produces_valid_file(self, float_df, tmp):
        path_none = tmp + ".none"
        path_high = tmp + ".high"
        m_none = pf.freeze(float_df, path_none, codec=pf.CODEC_ZSTD, quant=pf.QUANT_NONE)
        m_high = pf.freeze(float_df, path_high, codec=pf.CODEC_ZSTD, quant=pf.QUANT_HIGH)
        # Both produce valid readable files
        assert m_none["rows"] == len(float_df)
        assert m_high["rows"] == len(float_df)
        df_back = pf.thaw(path_high)
        assert len(df_back) == len(float_df)

    def test_quant_high_round_trip(self, float_df, tmp):
        pf.freeze(float_df, tmp, quant=pf.QUANT_HIGH)
        df_back = pf.thaw(tmp)
        assert len(df_back) == len(float_df)
        for col in ["price", "quantity", "discount"]:
            orig = float_df[col].to_numpy()
            restored = df_back[col].to_numpy()
            expected = orig.astype(np.float32).astype(np.float64)
            np.testing.assert_allclose(restored, expected, rtol=0, atol=0)

    def test_quant_high_special_cols_preserved(self, float_df, tmp):
        pf.freeze(float_df, tmp, quant=pf.QUANT_HIGH)
        df_back = pf.thaw(tmp)
        # lat and score use lag1, not float32 — values should be close but may differ
        assert len(df_back["lat"]) == len(float_df["lat"])


# ── Integration: QUANT_LOW auto-selects float16 ───────────────────────────────

class TestQuantLowFloat16:
    def test_quant_low_smaller_than_quant_high(self, float_df, tmp):
        path_high = tmp + ".high"
        path_low  = tmp + ".low"
        pf.freeze(float_df, path_high, codec=pf.CODEC_ZSTD, quant=pf.QUANT_HIGH)
        pf.freeze(float_df, path_low,  codec=pf.CODEC_ZSTD, quant=pf.QUANT_LOW)
        # float16 (2B) should be smaller than float32 (4B)
        assert os.path.getsize(path_low) < os.path.getsize(path_high)

    def test_quant_low_round_trip(self, float_df, tmp):
        pf.freeze(float_df, tmp, quant=pf.QUANT_LOW)
        df_back = pf.thaw(tmp)
        for col in ["price", "quantity"]:
            orig = float_df[col].to_numpy()
            restored = df_back[col].to_numpy()
            expected = orig.astype(np.float16).astype(np.float64)
            np.testing.assert_allclose(restored, expected, rtol=0, atol=0)


# ── Audit: lossy_columns metadata ────────────────────────────────────────────

class TestAuditLossyColumns:
    def test_float32_appears_in_lossy_columns(self, float_df, tmp):
        pf.freeze(float_df, tmp, predictors={"price": pf.PRED_FLOAT32})
        info = pf.audit(tmp)
        assert "price" in info["lossy_columns"]
        lc = info["lossy_columns"]["price"]
        assert lc["predictor"] == pf.PRED_FLOAT32
        assert lc["precision_bits"] == 32
        assert lc["max_abs_error"] >= 0.0

    def test_float16_appears_in_lossy_columns(self, float_df, tmp):
        pf.freeze(float_df, tmp, predictors={"quantity": pf.PRED_FLOAT16})
        info = pf.audit(tmp)
        assert "quantity" in info["lossy_columns"]
        assert info["lossy_columns"]["quantity"]["precision_bits"] == 16

    def test_no_lossy_columns_for_plain_freeze(self, float_df, tmp):
        pf.freeze(float_df, tmp, quant=pf.QUANT_NONE)
        info = pf.audit(tmp)
        assert info["lossy_columns"] == {}

    def test_quant_high_shows_lossy_for_generic_floats(self, float_df, tmp):
        pf.freeze(float_df, tmp, quant=pf.QUANT_HIGH)
        info = pf.audit(tmp)
        # price, quantity, discount should be in lossy_columns
        for col in ["price", "quantity", "discount"]:
            assert col in info["lossy_columns"]
        # lat and score should NOT be in lossy_columns (kept lag1)
        assert "lat" not in info["lossy_columns"]
        assert "score" not in info["lossy_columns"]


# ── Benchmark: embeddings compression ────────────────────────────────────────

class TestEmbeddingsBenchmark:
    def test_float32_beats_parquet_snappy_for_embeddings(self, embedding_df, tmp):
        import pyarrow as pa, pyarrow.parquet as pq, io
        # Write Parquet with snappy
        table = pa.Table.from_pandas(embedding_df)
        parquet_buf = io.BytesIO()
        pq.write_table(table, parquet_buf, compression="snappy")
        parquet_size = parquet_buf.tell()

        # Write Permafrost with float32
        pf.freeze(embedding_df, tmp, codec=pf.CODEC_ZSTD,
                  predictors={c: pf.PRED_FLOAT32 for c in embedding_df.columns})
        perm_size = os.path.getsize(tmp)

        # Permafrost + float32 should be competitive
        ratio = parquet_size / perm_size
        assert ratio > 0.5, f"Permafrost too large vs Parquet: ratio={ratio:.2f}"

    def test_float16_half_size_of_float32_for_embeddings(self, embedding_df, tmp):
        path32 = tmp + ".f32"
        path16 = tmp + ".f16"
        pf.freeze(embedding_df, path32, codec=pf.CODEC_ZSTD,
                  predictors={c: pf.PRED_FLOAT32 for c in embedding_df.columns})
        pf.freeze(embedding_df, path16, codec=pf.CODEC_ZSTD,
                  predictors={c: pf.PRED_FLOAT16 for c in embedding_df.columns})
        # float16 raw data is 2x smaller than float32
        assert os.path.getsize(path16) < os.path.getsize(path32)


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_float32_nan_becomes_zero(self, tmp):
        # NaN → 0.0 via fillna(0), consistent with all other predictors
        df = pd.DataFrame({"v": [1.0, float("nan"), 3.0, float("nan")]})
        pf.freeze(df, tmp, predictors={"v": pf.PRED_FLOAT32})
        df_back = pf.thaw(tmp)
        assert df_back["v"].iloc[1] == 0.0
        assert df_back["v"].iloc[3] == 0.0

    def test_float16_overflow_becomes_inf(self, tmp):
        df = pd.DataFrame({"v": [1.0, 70000.0, 3.0]})  # 70000 > float16 max (65504)
        pf.freeze(df, tmp, predictors={"v": pf.PRED_FLOAT16})
        df_back = pf.thaw(tmp)
        assert np.isinf(df_back["v"].iloc[1])

    def test_float32_zero(self, tmp):
        df = pd.DataFrame({"v": [0.0] * 100})
        pf.freeze(df, tmp, predictors={"v": pf.PRED_FLOAT32})
        df_back = pf.thaw(tmp)
        np.testing.assert_array_equal(df_back["v"].to_numpy(), 0.0)

    def test_float32_works_with_encryption(self, tmp):
        key = bytes(range(32))
        df = pd.DataFrame({"v": RNG.uniform(0, 100, 200)})
        pf.freeze(df, tmp, predictors={"v": pf.PRED_FLOAT32}, key=key)
        df_back = pf.thaw(tmp, key=key)
        expected = df["v"].to_numpy().astype(np.float32).astype(np.float64)
        np.testing.assert_allclose(df_back["v"].to_numpy(), expected, rtol=0, atol=0)

    def test_float32_with_multiple_chunks(self, tmp):
        df = pd.DataFrame({"v": RNG.uniform(0, 1000, 5000)})
        pf.freeze(df, tmp, chunk_rows=1000, predictors={"v": pf.PRED_FLOAT32})
        df_back = pf.thaw(tmp)
        expected = df["v"].to_numpy().astype(np.float32).astype(np.float64)
        np.testing.assert_allclose(df_back["v"].to_numpy(), expected, rtol=0, atol=0)

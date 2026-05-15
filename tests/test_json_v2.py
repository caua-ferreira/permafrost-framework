"""Tests for I5 — Preditor json_schema_v2."""
import json
import os
import tempfile

import pandas as pd
import pytest

import permafrost as pf
from permafrost.codec import (
    PRED_JSON_V2,
    PRED_RAW,
    _is_json_column,
    _json_v2_manifest,
    _encode_with_manifest,
    decode_column,
    freeze,
    thaw,
    CODEC_ZSTD,
    CODEC_LZMA2,
    QUANT_NONE,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _json_series(dicts, name="data"):
    return pd.Series([json.dumps(d) for d in dicts], name=name)


# ── _is_json_column ───────────────────────────────────────────────────────────

class TestIsJsonColumn:
    def test_all_json_dicts_returns_true(self):
        s = _json_series([{"a": 1}, {"b": 2}, {"a": 3}])
        assert _is_json_column(s) is True

    def test_plain_strings_returns_false(self):
        s = pd.Series(["hello", "world", "foo"])
        assert _is_json_column(s) is False

    def test_empty_series_returns_false(self):
        assert _is_json_column(pd.Series([], dtype=object)) is False

    def test_all_nulls_returns_false(self):
        assert _is_json_column(pd.Series([None, None, None])) is False

    def test_json_arrays_not_dicts_returns_false(self):
        s = pd.Series(['[1,2,3]', '[4,5]', '[6]'])
        assert _is_json_column(s) is False

    def test_mixed_below_threshold_returns_false(self):
        # 2 of 10 are dicts → 20% < 70%
        vals = ['{"a":1}', '{"b":2}'] + ['plain'] * 8
        assert _is_json_column(pd.Series(vals)) is False

    def test_mixed_above_threshold_returns_true(self):
        # 8 of 10 are dicts → 80% >= 70%
        vals = ['{"a":1}'] * 8 + ['plain', 'plain']
        assert _is_json_column(pd.Series(vals)) is True

    def test_threshold_exactly_met(self):
        # exactly 70%
        vals = ['{"a":1}'] * 7 + ['plain'] * 3
        assert _is_json_column(pd.Series(vals), threshold=0.70) is True

    def test_nulls_excluded_from_calculation(self):
        # 5 JSON dicts + 5 nulls → 5/5 = 100% non-null are dicts
        vals = [json.dumps({"x": i}) for i in range(5)] + [None] * 5
        s = pd.Series(vals)
        assert _is_json_column(s) is True

    def test_integer_series_returns_false(self):
        assert _is_json_column(pd.Series([1, 2, 3])) is False


# ── _json_v2_manifest ─────────────────────────────────────────────────────────

class TestJsonV2Manifest:
    def test_keys_extracted(self):
        s = _json_series([{"a": 1, "b": 2}, {"b": 3, "c": 4}])
        m = _json_v2_manifest("col", s)
        assert set(m["key_dict"]) == {"a", "b", "c"}

    def test_keys_sorted(self):
        s = _json_series([{"z": 1, "a": 2, "m": 3}])
        m = _json_v2_manifest("col", s)
        assert m["key_dict"] == sorted(m["key_dict"])

    def test_predictor_field(self):
        s = _json_series([{"x": 1}])
        m = _json_v2_manifest("col", s)
        assert m["predictor"] == PRED_JSON_V2

    def test_name_preserved(self):
        s = _json_series([{"x": 1}], name="events")
        m = _json_v2_manifest("events", s)
        assert m["name"] == "events"

    def test_empty_series_empty_key_dict(self):
        m = _json_v2_manifest("col", pd.Series([], dtype=object))
        assert m["key_dict"] == []

    def test_non_dict_values_ignored(self):
        s = pd.Series(['{"a":1}', 'not_json', None])
        m = _json_v2_manifest("col", s)
        assert m["key_dict"] == ["a"]

    def test_key_dict_no_duplicates(self):
        s = _json_series([{"a": 1}, {"a": 2}, {"a": 3}])
        m = _json_v2_manifest("col", s)
        assert m["key_dict"].count("a") == 1


# ── encode / decode roundtrip ─────────────────────────────────────────────────

class TestEncodeDecodeRoundtrip:
    def _roundtrip(self, series, manifest):
        encoded = _encode_with_manifest(series, manifest, QUANT_NONE)
        assert isinstance(encoded, bytes)
        decoded = decode_column(encoded, manifest, len(series))
        return decoded

    def test_basic_roundtrip(self):
        rows = [{"user": "alice", "score": 10}, {"user": "bob", "score": 20}]
        s = _json_series(rows)
        m = _json_v2_manifest("data", s)
        decoded = self._roundtrip(s, m)
        for i, orig in enumerate(rows):
            assert json.loads(decoded.iloc[i]) == orig

    def test_key_compression_happens(self):
        rows = [{"very_long_key_name": 1}]
        s = _json_series(rows)
        m = _json_v2_manifest("data", s)
        encoded = _encode_with_manifest(s, m, QUANT_NONE)
        raw_str = encoded.decode("utf-8")
        # The key should be replaced by its integer index "0"
        assert "very_long_key_name" not in raw_str
        assert '"0"' in raw_str or "0" in raw_str

    def test_nested_values_preserved(self):
        rows = [{"tags": [1, 2, 3], "meta": {"x": 1}}]
        s = _json_series(rows)
        m = _json_v2_manifest("data", s)
        decoded = self._roundtrip(s, m)
        assert json.loads(decoded.iloc[0]) == rows[0]

    def test_non_dict_value_passes_through(self):
        s = pd.Series(['{"a":1}', 'plain string', '{"b":2}'])
        m = {
            'name': 'col', 'dtype': 'object',
            'predictor': PRED_JSON_V2, 'scale': 1,
            'key_dict': ['a', 'b'],
        }
        decoded = self._roundtrip(s, m)
        assert json.loads(decoded.iloc[0]) == {"a": 1}
        assert decoded.iloc[1] == 'plain string'
        assert json.loads(decoded.iloc[2]) == {"b": 2}

    def test_null_value_as_string(self):
        s = pd.Series(['{"a":1}', 'None', '{"a":3}'])
        m = _json_v2_manifest("col", pd.Series(['{"a":1}']))
        decoded = self._roundtrip(s, m)
        assert json.loads(decoded.iloc[0]) == {"a": 1}

    def test_unknown_key_falls_back_to_string(self):
        # Key not in key_dict → kept as-is during encode and decode
        s = pd.Series(['{"known":1,"unknown_new_key":2}'])
        m = {
            'name': 'col', 'dtype': 'object',
            'predictor': PRED_JSON_V2, 'scale': 1,
            'key_dict': ['known'],
        }
        decoded = self._roundtrip(s, m)
        result = json.loads(decoded.iloc[0])
        assert result.get("known") == 1
        assert result.get("unknown_new_key") == 2

    def test_empty_dict_roundtrip(self):
        s = _json_series([{}])
        m = _json_v2_manifest("col", s)
        decoded = self._roundtrip(s, m)
        assert json.loads(decoded.iloc[0]) == {}

    def test_many_rows(self):
        import random
        random.seed(42)
        keys = ["alpha", "beta", "gamma", "delta"]
        rows = [{k: random.randint(0, 100) for k in random.sample(keys, 2)}
                for _ in range(1000)]
        s = _json_series(rows)
        m = _json_v2_manifest("data", s)
        decoded = self._roundtrip(s, m)
        assert len(decoded) == 1000
        for i, orig in enumerate(rows):
            assert json.loads(decoded.iloc[i]) == orig


# ── freeze / thaw integration ─────────────────────────────────────────────────

class TestFreezeThawIntegration:
    def _make_df(self, n=500):
        events = [json.dumps({"action": f"click_{i%5}", "ts": i, "value": i * 1.5})
                  for i in range(n)]
        return pd.DataFrame({"id": range(n), "event": events, "label": ["A"] * n})

    def test_json_column_detected_as_json_v2(self):
        df = self._make_df()
        with tempfile.NamedTemporaryFile(suffix=".permafrost", delete=False) as f:
            path = f.name
        try:
            freeze(df, path)
            info = pf.audit(path)
            stored = info["stored_schema"]
            # Check that 'event' got json_schema_v2
            from permafrost.codec import _read_header
            with open(path, "rb") as fh:
                raw = fh.read()
            h = _read_header(raw)
            assert h["manifests"]["event"]["predictor"] == PRED_JSON_V2
        finally:
            os.unlink(path)

    def test_freeze_thaw_roundtrip(self):
        df = self._make_df(200)
        with tempfile.NamedTemporaryFile(suffix=".permafrost", delete=False) as f:
            path = f.name
        try:
            freeze(df, path)
            df2 = thaw(path)
            assert len(df2) == len(df)
            for i in range(len(df)):
                orig = json.loads(df["event"].iloc[i])
                restored = json.loads(df2["event"].iloc[i])
                assert orig == restored
        finally:
            os.unlink(path)

    def test_json_v2_pre_compression_smaller_than_raw(self):
        # json_schema_v2 replaces long key names with integer indices in the
        # intermediate bytes (before the codec). This reduces pre-compression
        # size; a powerful codec like ZSTD-19 may erase the difference.
        long_keys = [
            "my_event_action_type_field_name",    # 30 chars → "0" (1 char)
            "current_user_identifier_field",      # 29 chars → "1"
            "computed_monetary_cents_value",      # 29 chars → "2"
        ]
        n = 100
        s = pd.Series(
            [json.dumps({k: (i if j > 0 else "click") for j, k in enumerate(long_keys)})
             for i in range(n)]
        )
        manifest_v2 = _json_v2_manifest("data", s)
        manifest_raw = {'name': 'data', 'dtype': 'object', 'predictor': PRED_RAW, 'scale': 1}

        bytes_v2 = _encode_with_manifest(s, manifest_v2, QUANT_NONE)
        bytes_raw = _encode_with_manifest(s, manifest_raw, QUANT_NONE)

        # Pre-compression bytes: json_schema_v2 must be smaller when keys are long
        assert len(bytes_v2) < len(bytes_raw)

    def test_manual_predictor_override_json_v2(self):
        # User can force PRED_JSON_V2 via predictors= arg
        df = pd.DataFrame({"col": ['{"x":1}', '{"x":2}']})
        with tempfile.NamedTemporaryFile(suffix=".permafrost", delete=False) as f:
            path = f.name
        try:
            freeze(df, path, predictors={"col": PRED_JSON_V2})
            df2 = thaw(path)
            assert json.loads(df2["col"].iloc[0]) == {"x": 1}
        finally:
            os.unlink(path)

    def test_chunked_roundtrip(self):
        n = 3000
        df = pd.DataFrame({
            "id": range(n),
            "payload": [json.dumps({"k": i % 10, "v": i}) for i in range(n)],
        })
        with tempfile.NamedTemporaryFile(suffix=".permafrost", delete=False) as f:
            path = f.name
        try:
            freeze(df, path, chunk_rows=500)
            df2 = thaw(path)
            assert len(df2) == n
            for i in range(n):
                assert json.loads(df2["payload"].iloc[i]) == {"k": i % 10, "v": i}
        finally:
            os.unlink(path)

    def test_codec_auto_with_json_column(self):
        df = self._make_df(300)
        with tempfile.NamedTemporaryFile(suffix=".permafrost", delete=False) as f:
            path = f.name
        try:
            metrics = freeze(df, path, codec="auto")
            df2 = thaw(path)
            assert len(df2) == len(df)
        finally:
            os.unlink(path)


# ── Exports ───────────────────────────────────────────────────────────────────

class TestExports:
    def test_PRED_JSON_V2_exported(self):
        assert pf.PRED_JSON_V2 == PRED_JSON_V2

    def test_PRED_JSON_V2_value(self):
        assert pf.PRED_JSON_V2 == 'json_schema_v2'

    def test_in_all(self):
        assert "PRED_JSON_V2" in pf.__all__

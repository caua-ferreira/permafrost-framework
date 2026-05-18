"""Tests for per_column_codec and codec_profile features."""
import os
import pytest
import numpy as np
import pandas as pd

import permafrost as pf
from permafrost.codec import CODEC_ZSTD, CODEC_LZMA2, CODEC_NONE, _select_codec_for_column


@pytest.fixture()
def small_df():
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        'id':          range(200),
        'amount':      rng.uniform(0, 100, 200),
        'category':    ['A', 'B', 'C', 'D'] * 50,
        'description': ['Short text for lzma2 testing ' * 3] * 200,
    })


# ── Basic round-trip ──────────────────────────────────────────────────────────

class TestPerColumnCodecBasic:
    def test_freeze_returns_per_col_flag(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        m = pf.freeze(small_df, path, per_column_codec={"description": "lzma2"})
        assert m['per_col_codec'] is True
        assert m['col_codecs']['description'] == 'lzma2'

    def test_roundtrip_fidelity(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path, per_column_codec={"description": "lzma2"})
        df_back = pf.unfreeze(path)
        assert list(df_back.columns) == list(small_df.columns)
        assert len(df_back) == len(small_df)
        # exact string columns
        for col in ['id', 'category', 'description']:
            assert list(df_back[col].astype(str)) == list(small_df[col].astype(str))
        # PRED_LAG1 uses scale=100 → 2 decimal places precision for generic floats
        np.testing.assert_allclose(
            df_back['amount'].to_numpy(dtype=float),
            small_df['amount'].to_numpy(dtype=float),
            atol=0.01,
        )

    def test_string_codec_name_accepted(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path,
                  codec="zstd",
                  per_column_codec={"description": "lzma2", "id": "zstd"})
        df_back = pf.unfreeze(path)
        assert len(df_back) == len(small_df)

    def test_int_codec_constant_accepted(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path,
                  per_column_codec={"description": CODEC_LZMA2})
        df_back = pf.unfreeze(path)
        assert len(df_back) == len(small_df)


# ── Codec profiles ────────────────────────────────────────────────────────────

class TestCodecProfile:
    def test_profile_max_speed_all_zstd(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path, codec_profile="max_speed")
        info = pf.audit(path)
        assert info['per_col_codec'] is True
        assert all(v == 'zstd' for v in info['col_codecs'].values())

    def test_profile_max_compression_all_lzma2(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path, codec_profile="max_compression")
        info = pf.audit(path)
        assert info['per_col_codec'] is True
        assert all(v == 'lzma2' for v in info['col_codecs'].values())

    def test_profile_balanced_lzma2_for_text(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path, codec_profile="balanced")
        info = pf.audit(path)
        assert info['per_col_codec'] is True
        assert info['col_codecs']['description'] == 'lzma2'
        assert info['col_codecs']['id'] == 'zstd'
        assert info['col_codecs']['amount'] == 'zstd'

    def test_profile_auto_roundtrip(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path, codec_profile="auto")
        df_back = pf.unfreeze(path)
        assert len(df_back) == len(small_df)

    def test_profile_overridden_by_per_column_codec(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path,
                  codec_profile="max_speed",
                  per_column_codec={"description": "lzma2"})
        info = pf.audit(path)
        assert info['col_codecs']['description'] == 'lzma2'
        assert info['col_codecs']['id'] == 'zstd'


# ── Backward compatibility ────────────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_old_file_no_per_col_codec_reads_correctly(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path)
        df_back = pf.unfreeze(path)
        assert len(df_back) == len(small_df)

    def test_audit_old_file_per_col_codec_false(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path)
        info = pf.audit(path)
        assert info['per_col_codec'] is False
        assert info['col_codecs'] is None


# ── Audit fields (version bump equivalent) ────────────────────────────────────

class TestAuditPerColCodec:
    def test_audit_reports_per_col_codec_true(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path, per_column_codec={"description": "lzma2"})
        info = pf.audit(path)
        assert info['per_col_codec'] is True
        assert info['col_codecs'] is not None
        assert info['col_codecs']['description'] == 'lzma2'

    def test_audit_codec_field_is_none_in_per_col_mode(self, small_df, tmp_path):
        path = str(tmp_path / "t.permafrost")
        pf.freeze(small_df, path, per_column_codec={"description": "lzma2"})
        info = pf.audit(path)
        assert info['codec'] == 'none'


# ── select_codec_for_column unit tests ───────────────────────────────────────

class TestSelectCodecForColumn:
    def test_int_column_gets_zstd(self):
        s = pd.Series(range(100))
        assert _select_codec_for_column('id', s, 'balanced') == CODEC_ZSTD

    def test_float_column_gets_zstd(self):
        s = pd.Series(np.random.rand(100))
        assert _select_codec_for_column('score', s, 'balanced') == CODEC_ZSTD

    def test_high_entropy_text_gets_lzma2(self):
        s = pd.Series(['This is a long text entry that should trigger lzma2 ' * 3] * 100)
        assert _select_codec_for_column('text', s, 'balanced') == CODEC_LZMA2

    def test_max_compression_always_lzma2(self):
        s = pd.Series(range(100))
        assert _select_codec_for_column('x', s, 'max_compression') == CODEC_LZMA2

    def test_max_speed_always_zstd(self):
        s = pd.Series(['abc'] * 100)
        assert _select_codec_for_column('x', s, 'max_speed') == CODEC_ZSTD

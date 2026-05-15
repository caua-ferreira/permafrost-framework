"""Tests for AES-256-GCM encryption — C1 feature (v0.7)."""
import os, tempfile, pytest
import numpy as np
import pandas as pd

import permafrost as pf
from permafrost.crypto import (
    LocalKeyProvider,
    KeyProvider,
    resolve_key,
    encrypt_chunk,
    decrypt_chunk,
    NONCE_SIZE,
    TAG_SIZE,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

KEY_32 = bytes(range(32))  # deterministic test key

@pytest.fixture
def sample_df():
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "id":    range(500),
        "valor": rng.uniform(0, 1000, 500).round(2),
        "cat":   np.random.choice(["A", "B", "C"], 500),
        "ts":    pd.date_range("2024-01-01", periods=500, freq="h"),
    })

@pytest.fixture
def tmp_path_permafrost(tmp_path):
    return str(tmp_path / "test.permafrost")


# ── Unit tests: crypto primitives ─────────────────────────────────────────────

class TestLocalKeyProvider:
    def test_accepts_32_bytes(self):
        kp = LocalKeyProvider(KEY_32)
        assert kp.get_key() == KEY_32

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError, match="32 bytes"):
            LocalKeyProvider(b"short")

    def test_key_id_is_hex_string(self):
        kp = LocalKeyProvider(KEY_32)
        kid = kp.key_id()
        assert len(kid) == 16
        int(kid, 16)  # must be valid hex

    def test_kms_name_is_local(self):
        assert LocalKeyProvider(KEY_32).kms_name == "local"

    def test_bytearray_accepted(self):
        kp = LocalKeyProvider(bytearray(KEY_32))
        assert kp.get_key() == KEY_32


class TestResolveKey:
    def test_none_returns_none_when_no_env(self, monkeypatch):
        monkeypatch.delenv("PERMAFROST_KEY", raising=False)
        raw, kms, kid = resolve_key(None)
        assert raw is None
        assert kms == ""
        assert kid == ""

    def test_bytes_wraps_in_local_provider(self):
        raw, kms, kid = resolve_key(KEY_32)
        assert raw == KEY_32
        assert kms == "local"
        assert len(kid) == 16

    def test_key_provider_passthrough(self):
        kp = LocalKeyProvider(KEY_32)
        raw, kms, kid = resolve_key(kp)
        assert raw == KEY_32
        assert kms == "local"

    def test_env_var_hex(self, monkeypatch):
        monkeypatch.setenv("PERMAFROST_KEY", KEY_32.hex())
        raw, kms, kid = resolve_key(None)
        assert raw == KEY_32

    def test_env_var_wrong_length_raises(self, monkeypatch):
        monkeypatch.setenv("PERMAFROST_KEY", "deadbeef")
        with pytest.raises(ValueError, match="32-byte"):
            resolve_key(None)

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError):
            resolve_key(12345)


class TestEncryptDecryptChunk:
    def test_round_trip(self):
        plaintext = b"hello permafrost" * 100
        blob = encrypt_chunk(plaintext, KEY_32)
        assert decrypt_chunk(blob, KEY_32) == plaintext

    def test_output_length(self):
        plaintext = b"x" * 256
        blob = encrypt_chunk(plaintext, KEY_32)
        assert len(blob) == NONCE_SIZE + len(plaintext) + TAG_SIZE

    def test_nonces_are_random(self):
        plaintext = b"same data"
        b1 = encrypt_chunk(plaintext, KEY_32)
        b2 = encrypt_chunk(plaintext, KEY_32)
        assert b1[:NONCE_SIZE] != b2[:NONCE_SIZE]

    def test_wrong_key_raises(self):
        blob = encrypt_chunk(b"secret", KEY_32)
        wrong_key = bytes([0] * 32)
        with pytest.raises(ValueError, match="(?i)decryption failed"):
            decrypt_chunk(blob, wrong_key)

    def test_tampered_ciphertext_raises(self):
        blob = encrypt_chunk(b"secret data", KEY_32)
        tampered = bytearray(blob)
        tampered[NONCE_SIZE + 4] ^= 0xFF
        with pytest.raises(ValueError, match="(?i)decryption failed"):
            decrypt_chunk(bytes(tampered), KEY_32)

    def test_tampered_tag_raises(self):
        blob = encrypt_chunk(b"secret data", KEY_32)
        tampered = bytearray(blob)
        tampered[-1] ^= 0xFF
        with pytest.raises(ValueError, match="(?i)decryption failed"):
            decrypt_chunk(bytes(tampered), KEY_32)

    def test_blob_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            decrypt_chunk(b"\x00" * 10, KEY_32)


# ── Integration: freeze + thaw with encryption ────────────────────────────────

class TestFreezeThawEncrypted:
    def test_round_trip_bytes_key(self, sample_df, tmp_path_permafrost):
        m = pf.freeze(sample_df, tmp_path_permafrost, codec=pf.CODEC_ZSTD, key=KEY_32)
        assert m["rows"] == 500
        df_back = pf.thaw(tmp_path_permafrost, key=KEY_32)
        assert len(df_back) == 500
        assert list(df_back.columns) == list(sample_df.columns)

    def test_round_trip_key_provider(self, sample_df, tmp_path_permafrost):
        kp = LocalKeyProvider(KEY_32)
        pf.freeze(sample_df, tmp_path_permafrost, key=kp)
        df_back = pf.thaw(tmp_path_permafrost, key=kp)
        assert len(df_back) == len(sample_df)

    def test_thaw_without_key_raises(self, sample_df, tmp_path_permafrost):
        pf.freeze(sample_df, tmp_path_permafrost, key=KEY_32)
        with pytest.raises(ValueError, match="encrypted"):
            pf.thaw(tmp_path_permafrost)

    def test_thaw_wrong_key_raises(self, sample_df, tmp_path_permafrost):
        pf.freeze(sample_df, tmp_path_permafrost, key=KEY_32)
        wrong = bytes([99] * 32)
        with pytest.raises(ValueError):
            pf.thaw(tmp_path_permafrost, key=wrong)

    def test_audit_shows_encrypted_true(self, sample_df, tmp_path_permafrost):
        pf.freeze(sample_df, tmp_path_permafrost, key=KEY_32)
        info = pf.audit(tmp_path_permafrost)
        assert info["encrypted"] is True
        assert info["kms"] == "local"
        assert len(info["key_id"]) == 16

    def test_audit_plaintext_shows_encrypted_false(self, sample_df, tmp_path_permafrost):
        pf.freeze(sample_df, tmp_path_permafrost)
        info = pf.audit(tmp_path_permafrost)
        assert info["encrypted"] is False

    def test_env_var_key(self, sample_df, tmp_path_permafrost, monkeypatch):
        monkeypatch.setenv("PERMAFROST_KEY", KEY_32.hex())
        pf.freeze(sample_df, tmp_path_permafrost)
        df_back = pf.thaw(tmp_path_permafrost)
        assert len(df_back) == len(sample_df)

    def test_key_ignored_for_plaintext_file(self, sample_df, tmp_path_permafrost):
        # freeze without key, thaw with key — should not fail (key is ignored)
        pf.freeze(sample_df, tmp_path_permafrost)
        df_back = pf.thaw(tmp_path_permafrost, key=KEY_32)
        assert len(df_back) == len(sample_df)

    def test_data_fidelity(self, sample_df, tmp_path_permafrost):
        pf.freeze(sample_df, tmp_path_permafrost, key=KEY_32)
        df_back = pf.thaw(tmp_path_permafrost, key=KEY_32)
        # compare values only — codec may return Categorical vs ArrowStringArray
        for col in sample_df.columns:
            orig = sample_df[col].astype(str).reset_index(drop=True)
            restored = df_back[col].astype(str).reset_index(drop=True)
            pd.testing.assert_series_equal(orig, restored, check_names=False)

    def test_partial_thaw_with_filter(self, tmp_path):
        df = pd.DataFrame({
            "ano": [2022] * 300 + [2023] * 300,
            "v":   range(600),
        })
        path = str(tmp_path / "part.permafrost")
        pf.freeze(df, path, partition_by="ano", key=KEY_32)
        df_2023 = pf.thaw(path, filter={"ano": 2023}, key=KEY_32)
        assert all(df_2023["ano"] == 2023)
        assert len(df_2023) == 300

    def test_sha256_verification_catches_tamper(self, sample_df, tmp_path_permafrost):
        pf.freeze(sample_df, tmp_path_permafrost, key=KEY_32)
        # corrupt a byte in the middle of the file
        with open(tmp_path_permafrost, "r+b") as f:
            f.seek(200)
            f.write(b"\xff")
        with pytest.raises(ValueError):
            pf.thaw(tmp_path_permafrost, key=KEY_32)

    def test_multiple_chunks(self, tmp_path):
        df = pd.DataFrame({"x": range(5_000)})
        path = str(tmp_path / "chunks.permafrost")
        pf.freeze(df, path, chunk_rows=1_000, key=KEY_32)
        info = pf.audit(path)
        assert info["n_chunks"] == 5
        assert info["encrypted"] is True
        df_back = pf.thaw(path, key=KEY_32)
        assert len(df_back) == 5_000


# ── Integration: freeze_stream + thaw_iter with encryption ────────────────────

class TestStreamEncrypted:
    def test_freeze_stream_round_trip(self, tmp_path):
        from permafrost import freeze_stream, thaw_iter
        chunks = [pd.DataFrame({"v": range(i * 100, (i + 1) * 100)}) for i in range(5)]
        path = str(tmp_path / "stream.permafrost")
        freeze_stream(iter(chunks), path, key=KEY_32)
        rows = sum(len(df) for df in thaw_iter(path, key=KEY_32))
        assert rows == 500

    def test_thaw_iter_without_key_raises(self, tmp_path):
        from permafrost import freeze_stream, thaw_iter
        chunks = [pd.DataFrame({"v": [1, 2, 3]})]
        path = str(tmp_path / "s.permafrost")
        freeze_stream(iter(chunks), path, key=KEY_32)
        with pytest.raises(ValueError, match="encrypted"):
            list(thaw_iter(path))

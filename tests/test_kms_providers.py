"""Tests for AWSKMSProvider and GCPKMSProvider — C1 (v0.7).

All KMS calls are mocked — no cloud credentials required.
boto3 and google-cloud-kms may not be installed; sys.modules patching is used
so the lazy imports inside get_key() are intercepted.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import permafrost as pf
from permafrost.crypto import (
    AWSKMSProvider,
    GCPKMSProvider,
    KeyProvider,
    LocalKeyProvider,
    resolve_key,
)

KEY_32 = bytes(range(32))
FAKE_EDK = b"\xde\xad\xbe\xef" * 8  # 32-byte fake KMS ciphertext


# ── mock factories ────────────────────────────────────────────────────────────

def _mock_boto3(plaintext: bytes, ciphertext: bytes):
    """Returns a mock boto3 module whose client() returns a KMS mock."""
    kms_client = MagicMock()
    kms_client.generate_data_key.return_value = {
        "Plaintext": plaintext,
        "CiphertextBlob": ciphertext,
    }
    kms_client.decrypt.return_value = {"Plaintext": plaintext}
    mock_module = MagicMock()
    mock_module.client.return_value = kms_client
    return mock_module, kms_client


def _mock_gcp_kms(plaintext: bytes, ciphertext: bytes):
    """Returns mock sys.modules entries for google.cloud.kms."""
    kms_client = MagicMock()
    encrypt_resp = MagicMock()
    encrypt_resp.ciphertext = ciphertext
    kms_client.encrypt.return_value = encrypt_resp
    decrypt_resp = MagicMock()
    decrypt_resp.plaintext = plaintext
    kms_client.decrypt.return_value = decrypt_resp

    kms_module = MagicMock()
    kms_module.KeyManagementServiceClient.return_value = kms_client
    google_cloud = MagicMock()
    google_cloud.kms = kms_module
    return {"boto3": None, "google": MagicMock(), "google.cloud": google_cloud, "google.cloud.kms": kms_module}, kms_client


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "id":    range(200),
        "value": [float(i) * 1.5 for i in range(200)],
        "label": ["A", "B"] * 100,
    })


# ── AWSKMSProvider unit tests ─────────────────────────────────────────────────

class TestAWSKMSProvider:
    ARN = "arn:aws:kms:us-east-1:123456789012:key/abc"

    def test_kms_name(self):
        assert AWSKMSProvider(self.ARN).kms_name == "aws-kms"

    def test_key_id_is_hex(self):
        kid = AWSKMSProvider(self.ARN).key_id()
        assert len(kid) == 16
        int(kid, 16)

    def test_key_id_deterministic(self):
        kp = AWSKMSProvider(self.ARN)
        assert kp.key_id() == kp.key_id()

    def test_generate_data_key_on_freeze(self):
        mock_boto3, kms_client = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            kp = AWSKMSProvider(self.ARN)
            key = kp.get_key()
        assert key == KEY_32
        kms_client.generate_data_key.assert_called_once_with(
            KeyId=self.ARN, KeySpec="AES_256"
        )

    def test_decrypt_on_thaw(self):
        mock_boto3, kms_client = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            kp = AWSKMSProvider(self.ARN, encrypted_dek=FAKE_EDK)
            key = kp.get_key()
        assert key == KEY_32
        kms_client.decrypt.assert_called_once()
        kms_client.generate_data_key.assert_not_called()

    def test_encrypted_dek_populated_after_get_key(self):
        mock_boto3, _ = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            kp = AWSKMSProvider(self.ARN)
            kp.get_key()
        assert kp.encrypted_dek == FAKE_EDK

    def test_encrypted_dek_empty_before_get_key(self):
        assert AWSKMSProvider(self.ARN).encrypted_dek == b""

    def test_set_encrypted_dek_injects_edek(self):
        mock_boto3, kms_client = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            kp = AWSKMSProvider(self.ARN)
            kp.set_encrypted_dek(FAKE_EDK)
            key = kp.get_key()
        assert key == KEY_32
        kms_client.decrypt.assert_called_once()
        kms_client.generate_data_key.assert_not_called()

    def test_set_encrypted_dek_noop_if_already_set(self):
        kp = AWSKMSProvider(self.ARN, encrypted_dek=FAKE_EDK)
        kp.set_encrypted_dek(b"\x00" * 32)
        assert kp.encrypted_dek == FAKE_EDK

    def test_key_cached_after_first_call(self):
        mock_boto3, kms_client = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            kp = AWSKMSProvider(self.ARN)
            kp.get_key()
            kp.get_key()
        assert kms_client.generate_data_key.call_count == 1

    def test_missing_boto3_raises_import_error(self):
        kp = AWSKMSProvider(self.ARN)
        kp._plaintext = None
        with patch.dict(sys.modules, {"boto3": None}):
            with pytest.raises((ImportError, ModuleNotFoundError)):
                kp.get_key()

    def test_is_key_provider_subclass(self):
        assert issubclass(AWSKMSProvider, KeyProvider)


# ── GCPKMSProvider unit tests ─────────────────────────────────────────────────

class TestGCPKMSProvider:
    KEY_NAME = "projects/my-project/locations/global/keyRings/my-ring/cryptoKeys/my-key"

    def test_kms_name(self):
        assert GCPKMSProvider(self.KEY_NAME).kms_name == "gcp-kms"

    def test_key_id_is_hex(self):
        kid = GCPKMSProvider(self.KEY_NAME).key_id()
        assert len(kid) == 16
        int(kid, 16)

    def test_encrypt_on_freeze(self):
        modules, kms_client = _mock_gcp_kms(KEY_32, FAKE_EDK)
        # side_effect returns first n bytes of KEY_32 so nonce size is respected
        with patch.dict(sys.modules, modules):
            with patch("os.urandom", side_effect=lambda n: KEY_32[:n]):
                kp = GCPKMSProvider(self.KEY_NAME)
                key = kp.get_key()
        assert key == KEY_32
        kms_client.encrypt.assert_called_once()

    def test_decrypt_on_thaw(self):
        modules, kms_client = _mock_gcp_kms(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, modules):
            kp = GCPKMSProvider(self.KEY_NAME, encrypted_dek=FAKE_EDK)
            key = kp.get_key()
        assert key == KEY_32
        kms_client.decrypt.assert_called_once()
        kms_client.encrypt.assert_not_called()

    def test_encrypted_dek_populated_after_freeze(self):
        modules, _ = _mock_gcp_kms(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, modules):
            with patch("os.urandom", side_effect=lambda n: KEY_32[:n]):
                kp = GCPKMSProvider(self.KEY_NAME)
                kp.get_key()
        assert kp.encrypted_dek == FAKE_EDK

    def test_set_encrypted_dek(self):
        kp = GCPKMSProvider(self.KEY_NAME)
        kp.set_encrypted_dek(FAKE_EDK)
        assert kp.encrypted_dek == FAKE_EDK

    def test_set_encrypted_dek_noop_if_already_set(self):
        kp = GCPKMSProvider(self.KEY_NAME, encrypted_dek=FAKE_EDK)
        kp.set_encrypted_dek(b"\x00" * 32)
        assert kp.encrypted_dek == FAKE_EDK

    def test_key_cached_after_first_call(self):
        modules, kms_client = _mock_gcp_kms(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, modules):
            with patch("os.urandom", side_effect=lambda n: KEY_32[:n]):
                kp = GCPKMSProvider(self.KEY_NAME)
                kp.get_key()
                kp.get_key()
        assert kms_client.encrypt.call_count == 1

    def test_is_key_provider_subclass(self):
        assert issubclass(GCPKMSProvider, KeyProvider)


# ── resolve_key with KMS providers ───────────────────────────────────────────

class TestResolveKeyWithKMS:
    ARN = "arn:aws:kms:us-east-1:123456789012:key/abc"

    def test_aws_provider_freeze_path(self):
        mock_boto3, kms_client = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            kp = AWSKMSProvider(self.ARN)
            raw, kms, kid, edek = resolve_key(kp)
        assert raw == KEY_32
        assert kms == "aws-kms"
        assert edek == FAKE_EDK

    def test_aws_provider_thaw_path_via_edek_hint(self):
        mock_boto3, kms_client = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            kp = AWSKMSProvider(self.ARN)
            raw, kms, kid, edek = resolve_key(kp, edek=FAKE_EDK)
        assert raw == KEY_32
        kms_client.generate_data_key.assert_not_called()
        kms_client.decrypt.assert_called_once()

    def test_edek_hint_not_injected_if_provider_already_has_edek(self):
        OTHER_EDK = b"\xca\xfe" * 16
        mock_boto3, kms_client = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            kp = AWSKMSProvider(self.ARN, encrypted_dek=FAKE_EDK)
            resolve_key(kp, edek=OTHER_EDK)
        assert kp.encrypted_dek == FAKE_EDK

    def test_local_provider_returns_empty_edek(self):
        _, kms, kid, edek = resolve_key(LocalKeyProvider(KEY_32))
        assert edek == b""


# ── Envelope encryption integration: freeze → thaw with EDK in file ──────────

class TestEnvelopeEncryptionIntegration:
    ARN = "arn:aws:kms:us-east-1:123456789012:key/abc"
    GCP_KEY = "projects/p/locations/global/keyRings/r/cryptoKeys/k"

    def test_aws_round_trip(self, sample_df, tmp_path):
        path = str(tmp_path / "enc_aws.permafrost")
        mock_boto3, _ = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            pf.freeze(sample_df, path, codec=pf.CODEC_ZSTD,
                      key=AWSKMSProvider(self.ARN))
            df_back = pf.unfreeze(path, key=AWSKMSProvider(self.ARN))
        assert len(df_back) == 200

    def test_edek_stored_in_file(self, sample_df, tmp_path):
        path = str(tmp_path / "enc_edek.permafrost")
        mock_boto3, _ = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            pf.freeze(sample_df, path, key=AWSKMSProvider(self.ARN))
        info = pf.audit(path)
        assert info["encrypted"] is True
        assert info["kms"] == "aws-kms"
        assert info["edek_size"] == len(FAKE_EDK)

    def test_audit_edek_size_zero_for_local_key(self, sample_df, tmp_path):
        path = str(tmp_path / "enc_local.permafrost")
        pf.freeze(sample_df, path, key=KEY_32)
        info = pf.audit(path)
        assert info["edek_size"] == 0

    def test_gcp_round_trip(self, sample_df, tmp_path):
        path = str(tmp_path / "enc_gcp.permafrost")
        modules, _ = _mock_gcp_kms(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, modules):
            # side_effect ensures os.urandom(n) returns exactly n bytes
            with patch("os.urandom", side_effect=lambda n: KEY_32[:n]):
                pf.freeze(sample_df, path, codec=pf.CODEC_ZSTD,
                          key=GCPKMSProvider(self.GCP_KEY))
            pf_thaw_key = GCPKMSProvider(self.GCP_KEY)
            df_back = pf.unfreeze(path, key=pf_thaw_key)
        assert len(df_back) == 200

    def test_aws_stream_round_trip(self, tmp_path):
        from permafrost import freeze_stream, peek
        path = str(tmp_path / "stream_aws.permafrost")
        chunks = [pd.DataFrame({"v": range(i * 50, (i + 1) * 50)}) for i in range(4)]
        mock_boto3, _ = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            freeze_stream(iter(chunks), path, key=AWSKMSProvider(self.ARN))
            total = sum(
                len(df) for df in peek(path, key=AWSKMSProvider(self.ARN))
            )
        assert total == 200

    def test_thaw_without_key_still_raises(self, sample_df, tmp_path):
        path = str(tmp_path / "enc_nokey.permafrost")
        mock_boto3, _ = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            pf.freeze(sample_df, path, key=AWSKMSProvider(self.ARN))
        with pytest.raises(ValueError, match="encrypted"):
            pf.unfreeze(path)

    def test_data_fidelity_aws(self, sample_df, tmp_path):
        path = str(tmp_path / "fid_aws.permafrost")
        mock_boto3, _ = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            pf.freeze(sample_df, path, key=AWSKMSProvider(self.ARN))
            df_back = pf.unfreeze(path, key=AWSKMSProvider(self.ARN))
        assert list(df_back.columns) == list(sample_df.columns)
        assert len(df_back) == len(sample_df)

    def test_multiple_chunks_aws(self, tmp_path):
        df = pd.DataFrame({"x": range(3_000)})
        path = str(tmp_path / "chunks_aws.permafrost")
        mock_boto3, _ = _mock_boto3(KEY_32, FAKE_EDK)
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            pf.freeze(df, path, chunk_rows=1_000, key=AWSKMSProvider(self.ARN))
            info = pf.audit(path)
            assert info["n_chunks"] == 3
            assert info["encrypted"] is True
            df_back = pf.unfreeze(path, key=AWSKMSProvider(self.ARN))
        assert len(df_back) == 3_000


# ── Exports ───────────────────────────────────────────────────────────────────

class TestExports:
    def test_aws_kms_provider_exported(self):
        from permafrost import AWSKMSProvider as A
        assert A is AWSKMSProvider

    def test_gcp_kms_provider_exported(self):
        from permafrost import GCPKMSProvider as G
        assert G is GCPKMSProvider

    def test_key_provider_abstract(self):
        with pytest.raises(TypeError):
            KeyProvider()

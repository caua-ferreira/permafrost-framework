"""
Mock-based tests for S3Adapter, GCSAdapter, AzureAdapter and LocalAdapter edge cases.
Uses sys.modules patching so cloud SDKs don't need to be installed.
"""
import os
import io
import tempfile
import shutil
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock, mock_open

import permafrost as pf
from permafrost.storage import (
    LocalAdapter, parse_uri, freeze_to, thaw_from, audit_remote, storage_from_uri,
)


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_df():
    np.random.seed(42)
    N = 200
    return pd.DataFrame({
        "id":    np.arange(1, N + 1, dtype=np.int32),
        "ano":   np.random.choice([2022, 2023], N).astype(np.int16),
        "valor": np.round(np.random.uniform(1, 999, N), 2),
        "cat":   np.random.choice(["A", "B", "C"], N),
    })


@pytest.fixture
def frozen_file(sample_df, tmp):
    path = os.path.join(tmp, "data.permafrost")
    pf.freeze(sample_df, path, codec=pf.CODEC_ZSTD)
    return path, sample_df


# ── LocalAdapter edge cases ────────────────────────────────────────────────────

class TestLocalAdapterEdgeCases:

    def test_resolve_non_local_scheme(self, tmp):
        adapter = LocalAdapter(tmp)
        # URI with non-local scheme is treated as subdir path
        resolved = adapter._resolve("s3://mybucket/mykey.permafrost")
        # Should not raise; returns a Path under base_dir
        assert resolved is not None

    def test_upload_resumable_resumes(self, tmp, frozen_file):
        pf_path, _ = frozen_file
        dst_uri = os.path.join(tmp, "subdir", "resumed.permafrost")
        adapter = LocalAdapter(tmp)
        # First upload
        r1 = adapter.upload_resumable(pf_path, dst_uri, chunk_size=1024)
        assert r1["resumed"] is False
        # Second upload — state should detect same file → but dst exists so it resumes
        # (bytes_done would be src_size, loop reads nothing → resumed=True but 0 written)
        state_file = pf_path + ".upload_state"
        # Manually corrupt state to simulate partial upload
        import json
        state = {
            "src_mtime": os.stat(pf_path).st_mtime,
            "src_size": os.stat(pf_path).st_size,
            "remote_uri": dst_uri,
            "bytes_written": 100,
        }
        with open(state_file, "w") as f:
            json.dump(state, f)
        r2 = adapter.upload_resumable(pf_path, dst_uri, chunk_size=1024)
        assert r2["resumed"] is True
        assert r2["size_bytes"] == os.stat(pf_path).st_size

    def test_list_returns_single_file(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        adapter = LocalAdapter(tmp)
        result = adapter.list(pf_path)
        assert pf_path in result

    def test_upload_and_verify_exception_in_read_header(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        dst_uri = os.path.join(tmp, "out.permafrost")
        adapter = LocalAdapter(tmp)
        # Patch read_header_bytes to raise so the except branch (lines 223-224) is hit
        with patch.object(adapter, "read_header_bytes", side_effect=Exception("boom")):
            result = adapter.upload_and_verify(pf_path, dst_uri)
        assert result["remote_magic_ok"] is None
        assert "local_sha256" in result

    def test_upload_permission_error_retry(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        dst = os.path.join(tmp, "perm.permafrost")
        adapter = LocalAdapter(tmp)
        call_count = [0]
        original_copy = shutil.copy2

        def flaky_copy(src, dst_path):
            call_count[0] += 1
            if call_count[0] < 3:
                raise PermissionError("access denied")
            return original_copy(src, dst_path)

        with patch("shutil.copy2", side_effect=flaky_copy):
            result = adapter.upload(pf_path, dst, show_progress=False)
        assert os.path.exists(dst)
        assert call_count[0] == 3

    def test_upload_permission_error_max_retries_exceeded(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        dst = os.path.join(tmp, "perm_fail.permafrost")
        adapter = LocalAdapter(tmp)

        with patch("shutil.copy2", side_effect=PermissionError("always fails")):
            with pytest.raises(PermissionError):
                adapter.upload(pf_path, dst, show_progress=False)


# ── S3Adapter ─────────────────────────────────────────────────────────────────

def _make_boto3_mock():
    """Build a minimal boto3 mock module with an s3 client."""
    mock_s3 = MagicMock()
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    # TransferConfig
    mock_transfer = MagicMock()
    mock_boto3.s3 = MagicMock()
    mock_boto3.s3.transfer = MagicMock()
    mock_boto3.s3.transfer.TransferConfig = MagicMock(return_value=MagicMock())
    return mock_boto3, mock_s3


class TestS3Adapter:

    def _adapter(self, mock_boto3, mock_s3):
        from permafrost.storage import S3Adapter
        modules = {
            "boto3": mock_boto3,
            "boto3.s3": mock_boto3.s3,
            "boto3.s3.transfer": mock_boto3.s3.transfer,
        }
        with patch.dict("sys.modules", modules):
            adapter = S3Adapter(region="us-east-1")
        adapter.s3 = mock_s3
        return adapter

    def test_init(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        from permafrost.storage import S3Adapter
        with patch.dict("sys.modules", {"boto3": mock_boto3, "boto3.s3": mock_boto3.s3, "boto3.s3.transfer": mock_boto3.s3.transfer}):
            adapter = S3Adapter(region="us-east-1")
        mock_boto3.client.assert_called_once()

    def test_parse_valid(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        bucket, key = adapter._parse("s3://my-bucket/path/file.permafrost")
        assert bucket == "my-bucket"
        assert key == "path/file.permafrost"

    def test_parse_invalid_scheme(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        with pytest.raises(ValueError, match="s3://"):
            adapter._parse("gs://wrong/key")

    def test_upload(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        result = adapter.upload(pf_path, "s3://bucket/key.permafrost", show_progress=False)
        assert result["adapter"] == "s3"
        assert result["bucket"] == "bucket"
        mock_s3.upload_file.assert_called_once()

    def test_upload_show_progress(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        mock_transfer_config = MagicMock()
        with patch.dict("sys.modules", {"boto3.s3.transfer": mock_boto3.s3.transfer}):
            result = adapter.upload(pf_path, "s3://bucket/key.permafrost", show_progress=True)
        assert result["adapter"] == "s3"

    def test_download(self, tmp):
        mock_boto3, mock_s3 = _make_boto3_mock()
        mock_s3.head_object.return_value = {"ContentLength": 1024}
        adapter = self._adapter(mock_boto3, mock_s3)
        local = os.path.join(tmp, "dl.permafrost")
        result = adapter.download("s3://bucket/key.permafrost", local, show_progress=False)
        assert result["size_bytes"] == 1024
        mock_s3.download_file.assert_called_once()

    def test_exists_true(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        mock_s3.head_object.return_value = {}
        assert adapter.exists("s3://bucket/key.permafrost") is True

    def test_exists_false(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        mock_s3.exceptions = MagicMock()
        mock_s3.exceptions.ClientError = Exception
        mock_s3.head_object.side_effect = Exception("not found")
        assert adapter.exists("s3://bucket/key.permafrost") is False

    def test_delete(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        assert adapter.delete("s3://bucket/key.permafrost") is True
        mock_s3.delete_object.assert_called_once()

    def test_list(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {"Contents": [{"Key": "data/file.permafrost"}, {"Key": "data/other.txt"}]},
        ]
        adapter = self._adapter(mock_boto3, mock_s3)
        result = adapter.list("s3://bucket/data/")
        assert "s3://bucket/data/file.permafrost" in result
        assert all(r.endswith(".permafrost") for r in result)

    def test_read_bytes(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(b"content")}
        adapter = self._adapter(mock_boto3, mock_s3)
        data = adapter.read_bytes("s3://bucket/key.permafrost")
        assert data == b"content"

    def test_read_header_bytes(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(b"PRMS" + b"\x00" * 100)}
        adapter = self._adapter(mock_boto3, mock_s3)
        data = adapter.read_header_bytes("s3://bucket/key.permafrost", n_bytes=104)
        assert data[:4] == b"PRMS"

    def test_read_footer_bytes(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        mock_s3.head_object.return_value = {"ContentLength": 8192}
        mock_s3.get_object.return_value = {"Body": io.BytesIO(b"footer_data")}
        adapter = self._adapter(mock_boto3, mock_s3)
        data = adapter.read_footer_bytes("s3://bucket/key.permafrost", n_bytes=512)
        assert data == b"footer_data"

    def test_write_bytes(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        result = adapter.write_bytes(b"hello", "s3://bucket/key.permafrost")
        mock_s3.put_object.assert_called_once()
        assert result["size_bytes"] == 5

    def test_upload_resumable(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        mock_boto3, mock_s3 = _make_boto3_mock()
        mock_s3.create_multipart_upload.return_value = {"UploadId": "uid123"}
        mock_s3.upload_part.return_value = {"ETag": '"etag1"'}
        adapter = self._adapter(mock_boto3, mock_s3)
        result = adapter.upload_resumable(
            pf_path, "s3://bucket/file.permafrost", chunk_size=5 * 1024 * 1024
        )
        assert result["adapter"] == "s3"
        assert result["resumed"] is False
        mock_s3.complete_multipart_upload.assert_called_once()

    def test_set_lifecycle(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        adapter = self._adapter(mock_boto3, mock_s3)
        adapter.set_lifecycle("my-bucket", "cold/", transition_days=30)
        mock_s3.put_bucket_lifecycle_configuration.assert_called_once()


# ── GCSAdapter ────────────────────────────────────────────────────────────────

def _make_gcs_mock():
    mock_client = MagicMock()
    mock_gcs = MagicMock()
    mock_gcs.Client.return_value = mock_client
    mock_storage_module = MagicMock()
    mock_storage_module.storage = mock_gcs
    return mock_gcs, mock_client


class TestGCSAdapter:

    def _adapter(self, mock_gcs, mock_client):
        from permafrost.storage import GCSAdapter
        modules = {
            "google": MagicMock(),
            "google.cloud": MagicMock(),
            "google.cloud.storage": mock_gcs,
        }
        with patch.dict("sys.modules", modules):
            adapter = GCSAdapter(project="my-project")
        adapter.client = mock_client
        return adapter

    def test_init(self):
        mock_gcs, mock_client = _make_gcs_mock()
        from permafrost.storage import GCSAdapter
        # Ensure the google.cloud mock has .storage pointing to our mock
        mock_google_cloud = MagicMock()
        mock_google_cloud.storage = mock_gcs
        modules = {
            "google": MagicMock(),
            "google.cloud": mock_google_cloud,
            "google.cloud.storage": mock_gcs,
        }
        with patch.dict("sys.modules", modules):
            # Also remove cached google.cloud from sys.modules to force re-import
            adapter = GCSAdapter()
        # Adapter was created without error
        assert adapter is not None

    def test_parse_valid(self):
        mock_gcs, mock_client = _make_gcs_mock()
        adapter = self._adapter(mock_gcs, mock_client)
        bucket, blob = adapter._parse("gs://my-bucket/path/file.permafrost")
        assert bucket == "my-bucket"
        assert blob == "path/file.permafrost"

    def test_parse_invalid_scheme(self):
        mock_gcs, mock_client = _make_gcs_mock()
        adapter = self._adapter(mock_gcs, mock_client)
        with pytest.raises(ValueError, match="gs://"):
            adapter._parse("s3://wrong/key")

    def test_upload(self, frozen_file):
        pf_path, _ = frozen_file
        mock_gcs, mock_client = _make_gcs_mock()
        adapter = self._adapter(mock_gcs, mock_client)
        result = adapter.upload(pf_path, "gs://bucket/key.permafrost", show_progress=False)
        assert result["adapter"] == "gcs"
        mock_client.bucket.return_value.blob.return_value.upload_from_filename.assert_called_once()

    def test_download(self, tmp):
        mock_gcs, mock_client = _make_gcs_mock()
        mock_blob = MagicMock()
        mock_blob.size = 512
        mock_client.bucket.return_value.blob.return_value = mock_blob
        adapter = self._adapter(mock_gcs, mock_client)
        local = os.path.join(tmp, "gcs_dl.permafrost")
        result = adapter.download("gs://bucket/key.permafrost", local, show_progress=False)
        assert result["size_bytes"] == 512
        mock_blob.download_to_filename.assert_called_once_with(local)

    def test_exists_true(self):
        mock_gcs, mock_client = _make_gcs_mock()
        mock_client.bucket.return_value.blob.return_value.exists.return_value = True
        adapter = self._adapter(mock_gcs, mock_client)
        assert adapter.exists("gs://bucket/key.permafrost") is True

    def test_exists_false(self):
        mock_gcs, mock_client = _make_gcs_mock()
        mock_client.bucket.return_value.blob.return_value.exists.return_value = False
        adapter = self._adapter(mock_gcs, mock_client)
        assert adapter.exists("gs://bucket/key.permafrost") is False

    def test_delete(self):
        mock_gcs, mock_client = _make_gcs_mock()
        adapter = self._adapter(mock_gcs, mock_client)
        assert adapter.delete("gs://bucket/key.permafrost") is True
        mock_client.bucket.return_value.blob.return_value.delete.assert_called_once()

    def test_list(self):
        mock_gcs, mock_client = _make_gcs_mock()
        mock_blob1 = MagicMock()
        mock_blob1.name = "data/file.permafrost"
        mock_blob2 = MagicMock()
        mock_blob2.name = "data/other.txt"
        mock_client.list_blobs.return_value = [mock_blob1, mock_blob2]
        adapter = self._adapter(mock_gcs, mock_client)
        result = adapter.list("gs://bucket/data/")
        assert any("file.permafrost" in r for r in result)
        assert not any(".txt" in r for r in result)

    def test_read_bytes(self):
        mock_gcs, mock_client = _make_gcs_mock()
        mock_client.bucket.return_value.blob.return_value.download_as_bytes.return_value = b"data"
        adapter = self._adapter(mock_gcs, mock_client)
        data = adapter.read_bytes("gs://bucket/key.permafrost")
        assert data == b"data"

    def test_read_header_bytes(self):
        mock_gcs, mock_client = _make_gcs_mock()
        mock_client.bucket.return_value.blob.return_value.download_as_bytes.return_value = b"PRMS" + b"\x00" * 200
        adapter = self._adapter(mock_gcs, mock_client)
        data = adapter.read_header_bytes("gs://bucket/key.permafrost", n_bytes=64)
        assert data[:4] == b"PRMS"

    def test_write_bytes(self):
        mock_gcs, mock_client = _make_gcs_mock()
        adapter = self._adapter(mock_gcs, mock_client)
        result = adapter.write_bytes(b"hello", "gs://bucket/key.permafrost")
        mock_client.bucket.return_value.blob.return_value.upload_from_string.assert_called_once_with(b"hello")
        assert result["adapter"] == "gcs"


# ── AzureAdapter ──────────────────────────────────────────────────────────────

def _make_azure_mock():
    mock_service_client = MagicMock()
    mock_azure_module = MagicMock()
    mock_azure_module.BlobServiceClient.from_connection_string.return_value = mock_service_client
    mock_azure_module.BlobServiceClient.return_value = mock_service_client
    return mock_azure_module, mock_service_client


class TestAzureAdapter:

    def _adapter(self, mock_azure, mock_service_client):
        from permafrost.storage import AzureAdapter
        modules = {
            "azure": MagicMock(),
            "azure.storage": MagicMock(),
            "azure.storage.blob": mock_azure,
        }
        with patch.dict("sys.modules", modules):
            adapter = AzureAdapter(conn_str="DefaultEndpointsProtocol=https;...")
        adapter.client = mock_service_client
        return adapter

    def test_init_conn_str(self):
        mock_azure, mock_service_client = _make_azure_mock()
        from permafrost.storage import AzureAdapter
        modules = {"azure": MagicMock(), "azure.storage": MagicMock(), "azure.storage.blob": mock_azure}
        with patch.dict("sys.modules", modules):
            adapter = AzureAdapter(conn_str="DefaultEndpoints...")
        mock_azure.BlobServiceClient.from_connection_string.assert_called_once()

    def test_init_account_name_key(self):
        mock_azure, mock_service_client = _make_azure_mock()
        from permafrost.storage import AzureAdapter
        modules = {"azure": MagicMock(), "azure.storage": MagicMock(), "azure.storage.blob": mock_azure}
        with patch.dict("sys.modules", modules):
            adapter = AzureAdapter(account_name="myaccount", account_key="mykey")
        mock_azure.BlobServiceClient.assert_called_once()

    def test_init_no_creds_raises(self):
        mock_azure, mock_service_client = _make_azure_mock()
        from permafrost.storage import AzureAdapter
        modules = {"azure": MagicMock(), "azure.storage": MagicMock(), "azure.storage.blob": mock_azure}
        with patch.dict("sys.modules", modules):
            with pytest.raises(ValueError):
                AzureAdapter()

    def test_parse_valid(self):
        mock_azure, mock_service = _make_azure_mock()
        adapter = self._adapter(mock_azure, mock_service)
        container, blob = adapter._parse("azure://mycontainer/path/file.permafrost")
        assert container == "mycontainer"
        assert blob == "path/file.permafrost"

    def test_parse_invalid_scheme(self):
        mock_azure, mock_service = _make_azure_mock()
        adapter = self._adapter(mock_azure, mock_service)
        with pytest.raises(ValueError, match="azure://"):
            adapter._parse("s3://wrong/key")

    def test_upload(self, frozen_file):
        pf_path, _ = frozen_file
        mock_azure, mock_service = _make_azure_mock()
        mock_blob_client = MagicMock()
        mock_service.get_blob_client.return_value = mock_blob_client
        adapter = self._adapter(mock_azure, mock_service)
        result = adapter.upload(pf_path, "azure://container/key.permafrost", show_progress=False)
        assert result["adapter"] == "azure"
        mock_blob_client.upload_blob.assert_called_once()

    def test_download(self, tmp):
        mock_azure, mock_service = _make_azure_mock()
        mock_blob_client = MagicMock()
        mock_props = MagicMock()
        mock_props.size = 2048
        mock_blob_client.get_blob_properties.return_value = mock_props
        mock_blob_client.download_blob.return_value.readall.return_value = b"x" * 2048
        mock_service.get_blob_client.return_value = mock_blob_client
        adapter = self._adapter(mock_azure, mock_service)
        local = os.path.join(tmp, "az_dl.permafrost")
        result = adapter.download("azure://container/key.permafrost", local, show_progress=False)
        assert result["size_bytes"] == 2048
        assert os.path.exists(local)

    def test_exists(self):
        mock_azure, mock_service = _make_azure_mock()
        mock_blob_client = MagicMock()
        mock_blob_client.exists.return_value = True
        mock_service.get_blob_client.return_value = mock_blob_client
        adapter = self._adapter(mock_azure, mock_service)
        assert adapter.exists("azure://container/key.permafrost") is True

    def test_delete(self):
        mock_azure, mock_service = _make_azure_mock()
        mock_blob_client = MagicMock()
        mock_service.get_blob_client.return_value = mock_blob_client
        adapter = self._adapter(mock_azure, mock_service)
        assert adapter.delete("azure://container/key.permafrost") is True
        mock_blob_client.delete_blob.assert_called_once()

    def test_list(self):
        mock_azure, mock_service = _make_azure_mock()
        mock_cc = MagicMock()
        blob1 = MagicMock(); blob1.name = "data/file.permafrost"
        blob2 = MagicMock(); blob2.name = "data/other.json"
        mock_cc.list_blobs.return_value = [blob1, blob2]
        mock_service.get_container_client.return_value = mock_cc
        adapter = self._adapter(mock_azure, mock_service)
        result = adapter.list("azure://container/data/")
        assert any("file.permafrost" in r for r in result)
        assert not any(".json" in r for r in result)

    def test_read_bytes(self):
        mock_azure, mock_service = _make_azure_mock()
        mock_blob_client = MagicMock()
        mock_blob_client.download_blob.return_value.readall.return_value = b"azure_content"
        mock_service.get_blob_client.return_value = mock_blob_client
        adapter = self._adapter(mock_azure, mock_service)
        data = adapter.read_bytes("azure://container/key.permafrost")
        assert data == b"azure_content"

    def test_write_bytes(self):
        mock_azure, mock_service = _make_azure_mock()
        mock_blob_client = MagicMock()
        mock_service.get_blob_client.return_value = mock_blob_client
        adapter = self._adapter(mock_azure, mock_service)
        result = adapter.write_bytes(b"hello azure", "azure://container/key.permafrost")
        mock_blob_client.upload_blob.assert_called_once_with(b"hello azure", overwrite=True)
        assert result["size_bytes"] == 11


# ── freeze_to / thaw_from / audit_remote with LocalAdapter ────────────────────

class TestHighLevelAPI:

    def test_freeze_to_local(self, sample_df, tmp):
        uri = os.path.join(tmp, "output.permafrost")
        metrics = freeze_to(sample_df, uri, codec=pf.CODEC_ZSTD)
        assert os.path.exists(uri)
        assert metrics["remote_magic_ok"] is True
        assert metrics["adapter"] == "local"

    def test_freeze_to_keep_local(self, sample_df, tmp):
        uri = os.path.join(tmp, "kept.permafrost")
        metrics = freeze_to(sample_df, uri, keep_local=True, codec=pf.CODEC_ZSTD)
        assert os.path.exists(uri)

    def test_thaw_from_local(self, frozen_file, tmp):
        pf_path, df_orig = frozen_file
        adapter = LocalAdapter(tmp)
        df_back = thaw_from(pf_path, adapter=adapter)
        assert len(df_back) == len(df_orig)

    def test_thaw_from_keep_local(self, frozen_file, tmp):
        pf_path, df_orig = frozen_file
        adapter = LocalAdapter(tmp)
        df_back = thaw_from(pf_path, adapter=adapter, keep_local=True)
        assert len(df_back) == len(df_orig)

    def test_audit_remote_local(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        info = audit_remote(pf_path)
        assert info["uri"] == pf_path
        assert "codec" in info
        assert "orig_rows" in info

    def test_audit_remote_invalid_magic(self, tmp):
        bad_path = os.path.join(tmp, "bad.permafrost")
        with open(bad_path, "wb") as f:
            f.write(b"NOTPRMS" + b"\x00" * 100)
        with pytest.raises(ValueError, match="válido"):
            audit_remote(bad_path)

    def test_audit_remote_index_parse_error(self, frozen_file, tmp):
        pf_path, _ = frozen_file
        adapter = LocalAdapter(tmp)
        # Patch read_footer_bytes to return garbage so index parse fails
        with patch.object(adapter, "read_footer_bytes", return_value=b"garbage_footer"):
            info = audit_remote(pf_path, adapter=adapter)
        # Should still succeed — bad index → idx = []
        assert info["n_index_entries"] == 0

    def test_storage_from_uri_s3(self):
        mock_boto3, mock_s3 = _make_boto3_mock()
        with patch.dict("sys.modules", {"boto3": mock_boto3, "boto3.s3": mock_boto3.s3, "boto3.s3.transfer": mock_boto3.s3.transfer}):
            adapter = storage_from_uri("s3://bucket/key")
        from permafrost.storage import S3Adapter
        assert isinstance(adapter, S3Adapter)

    def test_storage_from_uri_gcs(self):
        mock_gcs, mock_client = _make_gcs_mock()
        modules = {"google": MagicMock(), "google.cloud": MagicMock(), "google.cloud.storage": mock_gcs}
        with patch.dict("sys.modules", modules):
            adapter = storage_from_uri("gs://bucket/key")
        from permafrost.storage import GCSAdapter
        assert isinstance(adapter, GCSAdapter)

    def test_storage_from_uri_azure(self):
        mock_azure, mock_service = _make_azure_mock()
        modules = {"azure": MagicMock(), "azure.storage": MagicMock(), "azure.storage.blob": mock_azure}
        with patch.dict("sys.modules", modules):
            adapter = storage_from_uri("azure://container/key", conn_str="conn")
        from permafrost.storage import AzureAdapter
        assert isinstance(adapter, AzureAdapter)

    def test_storage_from_uri_local(self, tmp):
        adapter = storage_from_uri(os.path.join(tmp, "file.permafrost"))
        assert isinstance(adapter, LocalAdapter)

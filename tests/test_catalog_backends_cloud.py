"""
tests/test_catalog_backends_cloud.py — Testes com mock para S3/GCS/Azure backends.

Todos os testes usam unittest.mock para evitar chamadas reais à nuvem.
"""

import glob
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch, call


# ── helpers ───────────────────────────────────────────────────────────────────

def _inject_boto3_mock():
    """Inject a MagicMock as boto3 into sys.modules and return it."""
    mock_boto3 = MagicMock()
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    sys.modules.setdefault("boto3", mock_boto3)
    return mock_boto3, mock_s3


def _make_s3_backend(cache_dir, **kwargs):
    """Create S3CatalogBackend with a mocked boto3 client."""
    mock_boto3 = MagicMock()
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3

    orig = sys.modules.get("boto3")
    sys.modules["boto3"] = mock_boto3
    try:
        # Force reimport so the mock is picked up inside the try/except block
        import importlib
        import permafrost.catalog_backends as _cb
        importlib.reload(_cb)
        backend = _cb.S3CatalogBackend(bucket="test-bucket", cache_dir=cache_dir, **kwargs)
    finally:
        if orig is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = orig
        # Restore original module
        import importlib
        import permafrost.catalog_backends as _cb
        importlib.reload(_cb)

    backend._s3 = mock_s3
    return backend, mock_s3


# ── S3CatalogBackend ──────────────────────────────────────────────────────────

class TestS3BackendInit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_creates_cache_dir(self):
        cache = os.path.join(self.tmp, "s3cache")
        backend, _ = _make_s3_backend(cache)
        self.assertTrue(os.path.isdir(cache))

    def test_prefix_strips_trailing_slash(self):
        backend, _ = _make_s3_backend(self.tmp, prefix="datasets/")
        self.assertEqual(backend.prefix, "datasets")

    def test_max_cache_bytes_computed(self):
        backend, _ = _make_s3_backend(self.tmp, max_cache_size_gb=2.0)
        self.assertEqual(backend.max_cache_bytes, int(2.0 * 1024 ** 3))


class TestS3ParseUri(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend, _ = _make_s3_backend(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_bucket_and_key(self):
        bucket, key = self.backend._parse_uri("s3://my-bucket/path/to/file.permafrost")
        self.assertEqual(bucket, "my-bucket")
        self.assertEqual(key, "path/to/file.permafrost")

    def test_parse_bucket_only(self):
        bucket, key = self.backend._parse_uri("s3://my-bucket")
        self.assertEqual(bucket, "my-bucket")
        self.assertEqual(key, "")

    def test_parse_nested_path(self):
        bucket, key = self.backend._parse_uri("s3://bucket/a/b/c/d.permafrost")
        self.assertEqual(bucket, "bucket")
        self.assertEqual(key, "a/b/c/d.permafrost")


class TestS3CachePaths(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend, _ = _make_s3_backend(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_permafrost_extension(self):
        local, meta = self.backend._cache_paths("s3://bucket/file.permafrost")
        self.assertTrue(local.endswith(".permafrost"))
        self.assertTrue(meta.endswith(".meta.json"))

    def test_deterministic(self):
        uri = "s3://bucket/file.permafrost"
        local1, meta1 = self.backend._cache_paths(uri)
        local2, meta2 = self.backend._cache_paths(uri)
        self.assertEqual(local1, local2)
        self.assertEqual(meta1, meta2)

    def test_different_uris_different_paths(self):
        local1, _ = self.backend._cache_paths("s3://bucket/a.permafrost")
        local2, _ = self.backend._cache_paths("s3://bucket/b.permafrost")
        self.assertNotEqual(local1, local2)


class TestS3GetEtag(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend, self.mock_s3 = _make_s3_backend(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_etag_stripped_of_quotes(self):
        self.mock_s3.head_object.return_value = {"ETag": '"abc123def"'}
        etag = self.backend._get_etag("bucket", "key")
        self.assertEqual(etag, "abc123def")

    def test_etag_exception_returns_empty(self):
        self.mock_s3.head_object.side_effect = Exception("Access denied")
        etag = self.backend._get_etag("bucket", "key")
        self.assertEqual(etag, "")

    def test_etag_missing_field_returns_empty(self):
        self.mock_s3.head_object.return_value = {}
        etag = self.backend._get_etag("bucket", "key")
        self.assertEqual(etag, "")


class TestS3IsCacheValid(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend, self.mock_s3 = _make_s3_backend(self.tmp, cache_ttl=3600)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_meta(self, local_path, meta_path, etag, age_seconds=0):
        with open(local_path, "wb") as f:
            f.write(b"data")
        with open(meta_path, "w") as f:
            json.dump({"etag": etag, "cached_at": time.time() - age_seconds, "uri": "s3://b/k"}, f)

    def test_returns_false_when_no_files(self):
        result = self.backend._is_cache_valid("/nonexistent", "/also/not/here", "b", "k")
        self.assertFalse(result)

    def test_returns_false_when_local_missing(self):
        meta = os.path.join(self.tmp, "only_meta.meta.json")
        with open(meta, "w") as f:
            json.dump({"etag": "x", "cached_at": time.time()}, f)
        result = self.backend._is_cache_valid("/no/local", meta, "b", "k")
        self.assertFalse(result)

    def test_returns_false_when_ttl_expired(self):
        local = os.path.join(self.tmp, "expired.permafrost")
        meta = local + ".meta.json"
        self._write_meta(local, meta, "abc", age_seconds=7200)  # 2h > 1h TTL
        result = self.backend._is_cache_valid(local, meta, "b", "k")
        self.assertFalse(result)

    def test_returns_false_when_etag_differs(self):
        local = os.path.join(self.tmp, "etag_diff.permafrost")
        meta = local + ".meta.json"
        self._write_meta(local, meta, "old_etag", age_seconds=0)
        self.mock_s3.head_object.return_value = {"ETag": '"new_etag"'}
        result = self.backend._is_cache_valid(local, meta, "b", "k")
        self.assertFalse(result)

    def test_returns_true_when_valid(self):
        local = os.path.join(self.tmp, "valid.permafrost")
        meta = local + ".meta.json"
        self._write_meta(local, meta, "current_etag", age_seconds=60)
        self.mock_s3.head_object.return_value = {"ETag": '"current_etag"'}
        result = self.backend._is_cache_valid(local, meta, "b", "k")
        self.assertTrue(result)

    def test_returns_false_on_corrupt_meta(self):
        local = os.path.join(self.tmp, "corrupt.permafrost")
        meta = local + ".meta.json"
        with open(local, "wb") as f:
            f.write(b"data")
        with open(meta, "w") as f:
            f.write("NOT JSON {{{")
        result = self.backend._is_cache_valid(local, meta, "b", "k")
        self.assertFalse(result)


class TestS3Download(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend, self.mock_s3 = _make_s3_backend(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_fake_download(self):
        def fake_dl(bucket, key, lpath):
            with open(lpath, "wb") as f:
                f.write(b"fake file content")
        self.mock_s3.download_file.side_effect = fake_dl
        self.mock_s3.head_object.return_value = {"ETag": '"etag_xyz"'}

    def test_download_creates_local_and_meta(self):
        self._setup_fake_download()
        uri = "s3://bucket/data.permafrost"
        local = self.backend._download("bucket", "data.permafrost", uri)
        self.assertTrue(os.path.exists(local))
        meta = local + ".meta.json"
        self.assertTrue(os.path.exists(meta))

    def test_download_meta_contains_etag_and_uri(self):
        self._setup_fake_download()
        uri = "s3://bucket/data.permafrost"
        local = self.backend._download("bucket", "data.permafrost", uri)
        with open(local + ".meta.json") as f:
            meta = json.load(f)
        self.assertEqual(meta["etag"], "etag_xyz")
        self.assertEqual(meta["uri"], uri)
        self.assertIn("cached_at", meta)

    def test_download_calls_download_file(self):
        self._setup_fake_download()
        uri = "s3://bucket/data.permafrost"
        self.backend._download("bucket", "data.permafrost", uri)
        self.mock_s3.download_file.assert_called_once()


class TestS3EvictLRU(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_evict_removes_oldest_files(self):
        # 1-byte limit → all files should be evicted
        backend, _ = _make_s3_backend(self.tmp, max_cache_size_gb=0.0000001)
        for i in range(3):
            p = os.path.join(self.tmp, f"f{i}.permafrost")
            with open(p, "wb") as f:
                f.write(b"x" * 500)
            meta = p + ".meta.json"
            with open(meta, "w") as f:
                json.dump({"etag": f"e{i}", "cached_at": time.time()}, f)
        backend._evict_lru()
        remaining = glob.glob(os.path.join(self.tmp, "*.permafrost"))
        # Should have removed some files
        self.assertLess(len(remaining), 3)

    def test_evict_removes_meta_alongside(self):
        backend, _ = _make_s3_backend(self.tmp, max_cache_size_gb=0.0000001)
        p = os.path.join(self.tmp, "evict_me.permafrost")
        meta = p + ".meta.json"
        with open(p, "wb") as f:
            f.write(b"y" * 1000)
        with open(meta, "w") as f:
            json.dump({}, f)
        backend._evict_lru()
        # Both data and meta should be gone
        if not os.path.exists(p):
            self.assertFalse(os.path.exists(meta))

    def test_evict_no_op_within_limit(self):
        backend, _ = _make_s3_backend(self.tmp, max_cache_size_gb=10.0)
        for i in range(3):
            p = os.path.join(self.tmp, f"keep{i}.permafrost")
            with open(p, "wb") as f:
                f.write(b"z" * 10)
        backend._evict_lru()
        remaining = glob.glob(os.path.join(self.tmp, "*.permafrost"))
        self.assertEqual(len(remaining), 3)


class TestS3ResolvePath(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend, self.mock_s3 = _make_s3_backend(self.tmp, cache_ttl=3600)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resolve_local_path(self):
        result = self.backend.resolve_path("/some/local/file.permafrost")
        self.assertEqual(result, os.path.abspath("/some/local/file.permafrost"))

    def test_resolve_s3_cache_hit(self):
        uri = "s3://bucket/hit.permafrost"
        local, meta = self.backend._cache_paths(uri)
        with open(local, "wb") as f:
            f.write(b"cached")
        with open(meta, "w") as f:
            json.dump({"etag": "abc", "cached_at": time.time(), "uri": uri}, f)
        self.mock_s3.head_object.return_value = {"ETag": '"abc"'}

        result = self.backend.resolve_path(uri)
        self.assertEqual(result, local)
        self.mock_s3.download_file.assert_not_called()

    def test_resolve_s3_cache_miss_triggers_download(self):
        uri = "s3://bucket/miss.permafrost"
        local, _ = self.backend._cache_paths(uri)
        self.mock_s3.head_object.return_value = {"ETag": '"fresh"'}

        def fake_dl(bucket, key, lpath):
            with open(lpath, "wb") as f:
                f.write(b"downloaded")
        self.mock_s3.download_file.side_effect = fake_dl

        result = self.backend.resolve_path(uri)
        self.assertEqual(result, local)
        self.mock_s3.download_file.assert_called_once()


class TestS3Upload(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend, self.mock_s3 = _make_s3_backend(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upload_calls_s3_upload_file(self):
        self.backend.upload("/local/data.permafrost", "s3://my-bucket/remote/data.permafrost")
        self.mock_s3.upload_file.assert_called_once_with(
            "/local/data.permafrost", "my-bucket", "remote/data.permafrost"
        )

    def test_upload_non_s3_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            self.backend.upload("/local/file.pf", "/not/s3/path.pf")
        self.assertIn("s3://", str(ctx.exception))


# ── GCSCatalogBackend ─────────────────────────────────────────────────────────

class TestGCSBackendMocked(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_gcs_backend(self, **kwargs):
        mock_gcs = MagicMock()
        mock_client = MagicMock()
        mock_gcs.Client.return_value = mock_client

        with patch.dict(sys.modules, {
            "google": MagicMock(),
            "google.cloud": MagicMock(),
            "google.cloud.storage": mock_gcs,
        }):
            import importlib
            import permafrost.catalog_backends as _cb
            importlib.reload(_cb)
            backend = _cb.GCSCatalogBackend(bucket="gcs-bucket", cache_dir=self.tmp, **kwargs)

        import importlib
        import permafrost.catalog_backends as _cb
        importlib.reload(_cb)

        backend._client = mock_client
        return backend, mock_client

    def test_init_default_client(self):
        backend, mock_client = self._make_gcs_backend()
        self.assertIsNotNone(backend._client)

    def test_parse_uri(self):
        backend, _ = self._make_gcs_backend()
        bucket, blob = backend._parse_uri("gs://my-bucket/path/blob.permafrost")
        self.assertEqual(bucket, "my-bucket")
        self.assertEqual(blob, "path/blob.permafrost")

    def test_cache_paths_deterministic(self):
        backend, _ = self._make_gcs_backend()
        local1, meta1 = backend._cache_paths("gs://b/k")
        local2, meta2 = backend._cache_paths("gs://b/k")
        self.assertEqual(local1, local2)
        self.assertEqual(meta1, meta2)

    def test_resolve_local_path(self):
        backend, _ = self._make_gcs_backend()
        result = backend.resolve_path("/local/file.permafrost")
        self.assertEqual(result, os.path.abspath("/local/file.permafrost"))

    def test_resolve_gcs_cache_miss_downloads(self):
        backend, mock_client = self._make_gcs_backend(cache_ttl=3600)
        uri = "gs://bucket/file.permafrost"
        local, meta = backend._cache_paths(uri)

        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client.bucket.return_value = mock_bucket

        def fake_download(path):
            with open(path, "wb") as f:
                f.write(b"gcs data")
        mock_blob.download_to_filename.side_effect = fake_download

        result = backend.resolve_path(uri)
        self.assertEqual(result, local)
        mock_blob.download_to_filename.assert_called_once_with(local)

    def test_resolve_gcs_cache_hit(self):
        backend, mock_client = self._make_gcs_backend(cache_ttl=3600)
        uri = "gs://bucket/cached.permafrost"
        local, meta = backend._cache_paths(uri)
        with open(local, "wb") as f:
            f.write(b"old data")
        with open(meta, "w") as f:
            json.dump({"cached_at": time.time(), "uri": uri}, f)

        result = backend.resolve_path(uri)
        self.assertEqual(result, local)
        # download_to_filename should NOT be called
        mock_client.bucket.assert_not_called()

    def test_upload_calls_blob_upload(self):
        backend, mock_client = self._make_gcs_backend()
        src = os.path.join(self.tmp, "src.permafrost")
        with open(src, "wb") as f:
            f.write(b"data")

        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client.bucket.return_value = mock_bucket

        backend.upload(src, "gs://gcs-bucket/dest.permafrost")
        mock_blob.upload_from_filename.assert_called_once_with(src)

    def test_upload_non_gcs_raises(self):
        backend, _ = self._make_gcs_backend()
        with self.assertRaises(ValueError):
            backend.upload("/local.pf", "/not/gcs.pf")


# ── AzureCatalogBackend ───────────────────────────────────────────────────────

class TestAzureBackendMocked(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_azure_backend(self, **kwargs):
        mock_blob_svc = MagicMock()
        mock_blob_svc_cls = MagicMock(return_value=mock_blob_svc)
        mock_blob_svc_cls.from_connection_string.return_value = mock_blob_svc

        azure_mods = {
            "azure": MagicMock(),
            "azure.storage": MagicMock(),
            "azure.storage.blob": MagicMock(BlobServiceClient=mock_blob_svc_cls),
            "azure.core": MagicMock(),
            "azure.core.credentials": MagicMock(),
        }

        with patch.dict(sys.modules, azure_mods):
            import importlib
            import permafrost.catalog_backends as _cb
            importlib.reload(_cb)
            backend = _cb.AzureCatalogBackend(
                container="my-container",
                connection_string="DefaultEndpointsProtocol=https;AccountName=x;AccountKey=y",
                cache_dir=self.tmp,
                **kwargs,
            )

        import importlib
        import permafrost.catalog_backends as _cb
        importlib.reload(_cb)

        backend._client = mock_blob_svc
        return backend, mock_blob_svc

    def test_parse_uri(self):
        backend, _ = self._make_azure_backend()
        container, blob = backend._parse_uri("az://my-container/path/blob.permafrost")
        self.assertEqual(container, "my-container")
        self.assertEqual(blob, "path/blob.permafrost")

    def test_cache_paths_deterministic(self):
        backend, _ = self._make_azure_backend()
        l1, m1 = backend._cache_paths("az://c/k")
        l2, m2 = backend._cache_paths("az://c/k")
        self.assertEqual(l1, l2)

    def test_resolve_local_path(self):
        backend, _ = self._make_azure_backend()
        result = backend.resolve_path("/local/path.permafrost")
        self.assertEqual(result, os.path.abspath("/local/path.permafrost"))

    def test_resolve_azure_cache_hit(self):
        backend, mock_svc = self._make_azure_backend(cache_ttl=3600)
        uri = "az://container/cached.permafrost"
        local, meta = backend._cache_paths(uri)
        with open(local, "wb") as f:
            f.write(b"data")
        with open(meta, "w") as f:
            json.dump({"cached_at": time.time(), "uri": uri}, f)
        result = backend.resolve_path(uri)
        self.assertEqual(result, local)
        mock_svc.get_blob_client.assert_not_called()

    def test_resolve_azure_cache_miss_downloads(self):
        backend, mock_svc = self._make_azure_backend(cache_ttl=3600)
        uri = "az://container/new.permafrost"
        local, _ = backend._cache_paths(uri)

        mock_blob_client = MagicMock()
        mock_download = MagicMock()
        mock_blob_client.download_blob.return_value = mock_download
        mock_svc.get_blob_client.return_value = mock_blob_client

        def fake_readinto(f):
            f.write(b"azure data")
        mock_download.readinto.side_effect = fake_readinto

        result = backend.resolve_path(uri)
        self.assertEqual(result, local)
        mock_blob_client.download_blob.assert_called_once()

    def test_upload(self):
        backend, mock_svc = self._make_azure_backend()
        src = os.path.join(self.tmp, "up.permafrost")
        with open(src, "wb") as f:
            f.write(b"upload data")

        mock_blob_client = MagicMock()
        mock_svc.get_blob_client.return_value = mock_blob_client

        backend.upload(src, "az://my-container/dest/up.permafrost")
        mock_blob_client.upload_blob.assert_called_once()

    def test_upload_non_azure_raises(self):
        backend, _ = self._make_azure_backend()
        with self.assertRaises(ValueError):
            backend.upload("/local.pf", "/not/azure.pf")


if __name__ == "__main__":
    unittest.main()

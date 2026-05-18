"""
tests/test_catalog_backends.py — Testes para backends remotos do catálogo.

Cobre:
  - LocalCatalogBackend (resolve + upload)
  - S3/GCS/Azure: erro de importação ausente
  - PermafrostCatalog com backend injetado
  - register() com version, versions(), configure()
  - unfreeze() resolvendo via backend
  - integrity_check() resolvendo via backend
  - query.py: set_query_backend + register remoto
"""

import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

import permafrost as pf
from permafrost.catalog_backends import (
    CatalogBackend,
    LocalCatalogBackend,
)
from permafrost.catalog import PermafrostCatalog


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_df(n=200):
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "id":  np.arange(n, dtype=np.int64),
        "val": rng.random(n).astype(np.float32),
        "tag": np.where(np.arange(n) % 2 == 0, "even", "odd"),
    })


def _freeze_tmp(directory, filename="test.permafrost", n=200):
    """Cria um arquivo .permafrost no diretório e devolve o path."""
    path = os.path.join(directory, filename)
    pf.freeze(_make_df(n), path)
    return path


# ── LocalCatalogBackend ───────────────────────────────────────────────────────

class TestLocalCatalogBackend(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend = LocalCatalogBackend()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resolve_path_returns_abspath(self):
        src = _freeze_tmp(self.tmp)
        resolved = self.backend.resolve_path(src)
        self.assertEqual(resolved, os.path.abspath(src))

    def test_upload_copies_file(self):
        src = _freeze_tmp(self.tmp, "src.permafrost")
        dst = os.path.join(self.tmp, "subdir", "dst.permafrost")
        self.backend.upload(src, dst)
        self.assertTrue(os.path.exists(dst))
        self.assertEqual(os.path.getsize(src), os.path.getsize(dst))

    def test_is_remote_returns_false_for_local(self):
        self.assertFalse(self.backend.is_remote("/tmp/file.permafrost"))
        self.assertFalse(self.backend.is_remote("relative/path.pf"))

    def test_is_remote_returns_true_for_remote(self):
        self.assertTrue(self.backend.is_remote("s3://bucket/key.permafrost"))
        self.assertTrue(self.backend.is_remote("gs://bucket/key.permafrost"))
        self.assertTrue(self.backend.is_remote("az://container/blob.permafrost"))


# ── Backend ABC ───────────────────────────────────────────────────────────────

class TestCatalogBackendABC(unittest.TestCase):

    def test_cannot_instantiate_abc(self):
        with self.assertRaises(TypeError):
            CatalogBackend()  # abstract


# ── ImportError sem bibliotecas cloud ─────────────────────────────────────────

class TestMissingCloudDeps(unittest.TestCase):

    def test_s3_backend_raises_import_error_without_boto3(self):
        import sys
        orig = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # simulate missing
        try:
            from permafrost.catalog_backends import S3CatalogBackend
            with self.assertRaises(ImportError) as ctx:
                S3CatalogBackend(bucket="test")
            self.assertIn("boto3", str(ctx.exception))
            self.assertIn("pip install", str(ctx.exception))
        finally:
            if orig is None:
                sys.modules.pop("boto3", None)
            else:
                sys.modules["boto3"] = orig

    def test_gcs_backend_raises_import_error(self):
        import sys
        # Only run if google-cloud-storage is not installed
        try:
            import google.cloud.storage  # noqa: F401
            self.skipTest("google-cloud-storage is installed")
        except ImportError:
            pass
        from permafrost.catalog_backends import GCSCatalogBackend
        with self.assertRaises(ImportError) as ctx:
            GCSCatalogBackend(bucket="test")
        self.assertIn("google-cloud-storage", str(ctx.exception))

    def test_azure_backend_raises_import_error(self):
        import sys
        try:
            import azure.storage.blob  # noqa: F401
            self.skipTest("azure-storage-blob is installed")
        except ImportError:
            pass
        from permafrost.catalog_backends import AzureCatalogBackend
        with self.assertRaises(ImportError) as ctx:
            AzureCatalogBackend(container="test", connection_string="x")
        self.assertIn("azure-storage-blob", str(ctx.exception))


# ── PermafrostCatalog + backend ────────────────────────────────────────────────

class TestCatalogWithBackend(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend = LocalCatalogBackend()
        self.cat = PermafrostCatalog(":memory:", backend=self.backend)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_accepts_backend(self):
        self.assertIsInstance(self.cat._backend, LocalCatalogBackend)

    def test_init_default_backend_is_local(self):
        cat2 = PermafrostCatalog(":memory:")
        self.assertIsInstance(cat2._backend, LocalCatalogBackend)

    def test_configure_replaces_backend(self):
        new_backend = LocalCatalogBackend()
        self.cat.configure(new_backend)
        self.assertIs(self.cat._backend, new_backend)

    def test_register_with_version(self):
        path = _freeze_tmp(self.tmp)
        result = self.cat.register(path, version="v1.0")
        self.assertEqual(result["status"], "registered")
        self.assertEqual(result["version"], "v1.0")

    def test_register_version_stored_in_db(self):
        path = _freeze_tmp(self.tmp)
        self.cat.register(path, version="v2.5")
        df = self.cat.versions("test")
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["version"], "v2.5")

    def test_versions_method_returns_dataframe(self):
        p1 = _freeze_tmp(self.tmp, "a.permafrost")
        p2 = _freeze_tmp(self.tmp, "a_v2.permafrost")
        self.cat.register(p1, name="dataset_a", version="v1")
        self.cat.register(p2, name="dataset_a", version="v2")
        df = self.cat.versions("dataset_a")
        self.assertEqual(len(df), 2)
        versions = set(df["version"].tolist())
        self.assertEqual(versions, {"v1", "v2"})

    def test_register_without_version_is_null(self):
        path = _freeze_tmp(self.tmp)
        self.cat.register(path)
        df = self.cat.versions("test")
        self.assertTrue(df.iloc[0]["version"] is None or pd.isna(df.iloc[0]["version"]))

    def test_unfreeze_resolves_via_backend(self):
        path = _freeze_tmp(self.tmp)
        self.cat.register(path, name="myds")
        df = self.cat.unfreeze("myds")
        self.assertEqual(len(df), 200)
        self.assertIn("id", df.columns)

    def test_unfreeze_with_version_selects_correct_file(self):
        orig_df = _make_df(100)
        new_df  = _make_df(50)
        p1 = os.path.join(self.tmp, "v1.permafrost")
        p2 = os.path.join(self.tmp, "v2.permafrost")
        pf.freeze(orig_df, p1)
        pf.freeze(new_df,  p2)
        self.cat.register(p1, name="versioned", version="v1")
        self.cat.register(p2, name="versioned", version="v2")
        df_v1 = self.cat.unfreeze("versioned", version="v1")
        df_v2 = self.cat.unfreeze("versioned", version="v2")
        self.assertEqual(len(df_v1), 100)
        self.assertEqual(len(df_v2), 50)

    def test_integrity_check_resolves_via_backend(self):
        path = _freeze_tmp(self.tmp)
        self.cat.register(path, name="ic_test")
        result = self.cat.integrity_check("ic_test")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["status"], "OK")

    def test_integrity_check_file_missing(self):
        path = _freeze_tmp(self.tmp)
        self.cat.register(path, name="gone")
        os.remove(path)
        result = self.cat.integrity_check("gone")
        self.assertEqual(result.iloc[0]["status"], "FILE_MISSING")


# ── query.py: set_query_backend ───────────────────────────────────────────────

class TestSetQueryBackend(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Reset global backend and registry after each test
        pf.set_query_backend(None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        pf.set_query_backend(None)
        # Clean up any registered aliases
        for alias in list(pf.registered().keys()):
            pf.unregister(alias)

    def test_set_query_backend_local(self):
        backend = LocalCatalogBackend()
        pf.set_query_backend(backend)
        # No error; local backend resolves normally
        path = _freeze_tmp(self.tmp)
        pf.register("qtest", path)
        df = pf.query("SELECT COUNT(*) AS n FROM qtest")
        self.assertEqual(df.iloc[0]["n"], 200)

    def test_register_remote_raises_without_backend(self):
        """Remote URI with no backend raises ValueError when query runs."""
        # Registration itself should work (no existence check for remote)
        pf.register("remote_alias", "s3://bucket/data.permafrost")
        # Running the query should raise (backend not set → no resolution)
        with self.assertRaises((ValueError, Exception)):
            pf.query("SELECT * FROM remote_alias LIMIT 1")

    def test_set_query_backend_none_resets(self):
        pf.set_query_backend(LocalCatalogBackend())
        pf.set_query_backend(None)
        # Local files still work (they use os.path.abspath directly)
        path = _freeze_tmp(self.tmp)
        pf.register("reset_test", path)
        df = pf.query("SELECT COUNT(*) AS n FROM reset_test")
        self.assertEqual(df.iloc[0]["n"], 200)


# ── Public API exports ────────────────────────────────────────────────────────

class TestPublicAPIExports(unittest.TestCase):

    def test_catalog_backend_exported(self):
        self.assertTrue(hasattr(pf, "CatalogBackend"))
        self.assertTrue(hasattr(pf, "LocalCatalogBackend"))
        self.assertTrue(hasattr(pf, "S3CatalogBackend"))
        self.assertTrue(hasattr(pf, "GCSCatalogBackend"))
        self.assertTrue(hasattr(pf, "AzureCatalogBackend"))

    def test_set_query_backend_exported(self):
        self.assertTrue(hasattr(pf, "set_query_backend"))
        self.assertTrue(callable(pf.set_query_backend))


if __name__ == "__main__":
    unittest.main()

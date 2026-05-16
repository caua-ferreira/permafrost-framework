"""
Tests for PermafrostContext — high-level unified API.
"""
import os
import shutil
import tempfile
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

import permafrost as pf
from permafrost.context import PermafrostContext


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_df():
    np.random.seed(42)
    N = 400
    return pd.DataFrame({
        "id":    np.arange(1, N + 1, dtype=np.int32),
        "ano":   np.random.choice([2022, 2023, 2024], N).astype(np.int16),
        "valor": np.round(np.random.uniform(10, 9999, N), 2),
        "cat":   np.random.choice(["A", "B", "C"], N),
    })


# ── Criação e repr ────────────────────────────────────────────────────────────

class TestContextCreation:

    def test_no_args(self):
        ctx = PermafrostContext()
        assert ctx.catalog_path is None
        assert ctx.storage_uri is None
        assert ctx.cluster_url is None

    def test_with_catalog(self, tmp):
        ctx = PermafrostContext(catalog=os.path.join(tmp, "cat.db"))
        assert ctx.catalog_path.endswith("cat.db")

    def test_repr_empty(self):
        ctx = PermafrostContext()
        r = repr(ctx)
        assert "PermafrostContext" in r

    def test_repr_with_args(self, tmp):
        ctx = PermafrostContext(
            catalog=os.path.join(tmp, "c.db"),
            storage="s3://bucket/",
            cluster="http://master:8700",
        )
        r = repr(ctx)
        assert "catalog" in r
        assert "storage" in r
        assert "cluster" in r

    def test_default_codec_lzma2(self):
        ctx = PermafrostContext()
        assert ctx.default_codec == pf.CODEC_LZMA2

    def test_custom_codec(self):
        ctx = PermafrostContext(codec=pf.CODEC_ZSTD)
        assert ctx.default_codec == pf.CODEC_ZSTD

    def test_exported_from_package(self):
        assert hasattr(pf, "PermafrostContext")
        assert pf.PermafrostContext is PermafrostContext


# ── Adapter lazy loading ──────────────────────────────────────────────────────

class TestAdapterLazy:

    def test_local_adapter_without_storage(self, tmp):
        ctx = PermafrostContext()
        from permafrost.storage import LocalAdapter
        assert isinstance(ctx.adapter, LocalAdapter)

    def test_adapter_cached(self, tmp):
        ctx = PermafrostContext()
        a1 = ctx.adapter
        a2 = ctx.adapter
        assert a1 is a2

    def test_catalog_raises_without_path(self):
        ctx = PermafrostContext()
        with pytest.raises(RuntimeError, match="Catalog não configurado"):
            _ = ctx.catalog

    def test_client_raises_without_cluster(self):
        ctx = PermafrostContext()
        with pytest.raises(RuntimeError, match="Cluster não configurado"):
            _ = ctx.client


# ── URI resolution ────────────────────────────────────────────────────────────

class TestURIResolution:

    def test_name_without_extension(self):
        ctx = PermafrostContext()
        assert ctx._resolve_uri("vendas") == "vendas.permafrost"

    def test_name_with_extension(self):
        ctx = PermafrostContext()
        assert ctx._resolve_uri("vendas.permafrost") == "vendas.permafrost"

    def test_with_s3_storage(self):
        ctx = PermafrostContext(storage="s3://bucket/cold/")
        uri = ctx._resolve_uri("vendas_2024")
        assert uri == "s3://bucket/cold/vendas_2024.permafrost"

    def test_with_trailing_slash(self):
        ctx = PermafrostContext(storage="gs://bucket/prefix/")
        uri = ctx._resolve_uri("data")
        assert uri.startswith("gs://bucket/prefix/")

    def test_is_remote_s3(self):
        ctx = PermafrostContext(storage="s3://bucket/")
        assert ctx._is_remote() is True

    def test_is_remote_local(self, tmp):
        ctx = PermafrostContext(storage=tmp)
        assert ctx._is_remote() is False

    def test_is_remote_no_storage(self):
        ctx = PermafrostContext()
        assert ctx._is_remote() is False


# ── freeze (local) ────────────────────────────────────────────────────────────

class TestFreezeLocal:

    def test_freeze_creates_file(self, sample_df, tmp):
        ctx = PermafrostContext()
        out = os.path.join(tmp, "out.permafrost")
        metrics = ctx.freeze(sample_df, out)
        assert os.path.exists(out)
        assert metrics["rows"] == len(sample_df)

    def test_freeze_with_zstd(self, sample_df, tmp):
        ctx = PermafrostContext(codec=pf.CODEC_ZSTD)
        out = os.path.join(tmp, "zstd.permafrost")
        metrics = ctx.freeze(sample_df, out)
        info = pf.audit(out)
        assert info["codec"] == "zstd"

    def test_freeze_codec_override(self, sample_df, tmp):
        ctx = PermafrostContext(codec=pf.CODEC_LZMA2)
        out = os.path.join(tmp, "override.permafrost")
        ctx.freeze(sample_df, out, codec=pf.CODEC_ZSTD)
        info = pf.audit(out)
        assert info["codec"] == "zstd"

    def test_freeze_with_partition(self, sample_df, tmp):
        ctx = PermafrostContext()
        out = os.path.join(tmp, "part.permafrost")
        ctx.freeze(sample_df, out, partition_by="ano")
        info = pf.audit(out)
        assert info["partition_col"] == "ano"

    def test_freeze_returns_uri(self, sample_df, tmp):
        ctx = PermafrostContext()
        out = os.path.join(tmp, "uri_test.permafrost")
        metrics = ctx.freeze(sample_df, out)
        assert "uri" in metrics
        assert metrics["uri"] == out

    def test_freeze_registers_in_catalog(self, sample_df, tmp):
        db = os.path.join(tmp, "cat.db")
        ctx = PermafrostContext(catalog=db)
        out = os.path.join(tmp, "reg.permafrost")
        ctx.freeze(sample_df, out)
        results = ctx.search()
        assert len(results) >= 1

    def test_freeze_with_storage_prefix(self, sample_df, tmp):
        ctx = PermafrostContext(storage=tmp)
        metrics = ctx.freeze(sample_df, "vendas")
        expected = os.path.join(tmp, "vendas.permafrost")
        assert os.path.exists(expected)
        assert metrics["uri"].endswith("vendas.permafrost")


# ── thaw ─────────────────────────────────────────────────────────────────────

class TestThaw:

    @pytest.fixture
    def frozen(self, sample_df, tmp):
        ctx = PermafrostContext()
        out = os.path.join(tmp, "data.permafrost")
        ctx.freeze(sample_df, out)
        return out, sample_df

    def test_thaw_roundtrip(self, frozen, tmp):
        path, df_orig = frozen
        ctx = PermafrostContext()
        df_back = ctx.thaw(path)
        assert len(df_back) == len(df_orig)
        assert set(df_back.columns) == set(df_orig.columns)

    def test_thaw_with_filter(self, sample_df, tmp):
        ctx = PermafrostContext()
        out = os.path.join(tmp, "partitioned.permafrost")
        ctx.freeze(sample_df, out, partition_by="ano", chunk_rows=100)
        df_back = ctx.thaw(out, filter={"ano": 2022})
        assert len(df_back) > 0
        assert (df_back["ano"] == 2022).all()

    def test_thaw_auto_extension(self, frozen, tmp):
        path, df_orig = frozen
        ctx = PermafrostContext(storage=tmp)
        df_back = ctx.thaw("data")  # without .permafrost
        assert len(df_back) == len(df_orig)


# ── audit ─────────────────────────────────────────────────────────────────────

class TestAudit:

    def test_audit_returns_metadata(self, sample_df, tmp):
        ctx = PermafrostContext()
        out = os.path.join(tmp, "audit.permafrost")
        ctx.freeze(sample_df, out)
        info = ctx.audit(out)
        assert "codec" in info
        assert info["orig_rows"] == len(sample_df)

    def test_audit_with_storage(self, sample_df, tmp):
        ctx = PermafrostContext(storage=tmp)
        ctx.freeze(sample_df, "audit_test")
        info = ctx.audit("audit_test")
        assert info["orig_rows"] == len(sample_df)


# ── list ──────────────────────────────────────────────────────────────────────

class TestList:

    def test_list_local(self, sample_df, tmp):
        ctx = PermafrostContext(storage=tmp)
        ctx.freeze(sample_df, "file1")
        ctx.freeze(sample_df, "file2")
        result = ctx.list()
        pf_files = [r for r in result if r.endswith(".permafrost")]
        assert len(pf_files) >= 2

    def test_list_no_storage(self):
        ctx = PermafrostContext()
        result = ctx.list()
        assert isinstance(result, list)


# ── catalog delegation ────────────────────────────────────────────────────────

class TestCatalogDelegation:

    @pytest.fixture
    def ctx_with_data(self, sample_df, tmp):
        db = os.path.join(tmp, "cat.db")
        ctx = PermafrostContext(catalog=db)
        out = os.path.join(tmp, "data.permafrost")
        pf.freeze(sample_df, out, codec=pf.CODEC_ZSTD, partition_by="ano")
        ctx.register(out)
        return ctx, out, sample_df

    def test_register(self, sample_df, tmp):
        db = os.path.join(tmp, "cat.db")
        ctx = PermafrostContext(catalog=db)
        out = os.path.join(tmp, "reg.permafrost")
        pf.freeze(sample_df, out)
        result = ctx.register(out)
        assert result["status"] == "registered"

    def test_search(self, ctx_with_data):
        ctx, _, _ = ctx_with_data
        result = ctx.search()
        assert len(result) >= 1

    def test_search_by_codec(self, ctx_with_data):
        ctx, _, _ = ctx_with_data
        result = ctx.search(codec="zstd")
        assert len(result) >= 1

    def test_cost_report(self, ctx_with_data):
        ctx, _, _ = ctx_with_data
        report = ctx.cost_report("glacier_deep")
        assert "cost_monthly_usd" in report.columns

    def test_integrity_check(self, ctx_with_data):
        ctx, _, _ = ctx_with_data
        result = ctx.integrity_check()
        assert len(result) >= 1
        assert "status" in result.columns

    def test_stats(self, ctx_with_data):
        ctx, _, _ = ctx_with_data
        s = ctx.stats()
        assert s["total_datasets"] >= 1
        assert "total_rows" in s

    def test_sql(self, ctx_with_data):
        ctx, _, _ = ctx_with_data
        result = ctx.sql("SELECT COUNT(*) as n FROM datasets")
        assert result["n"].iloc[0] >= 1

    def test_search_without_catalog_raises(self):
        ctx = PermafrostContext()
        with pytest.raises(RuntimeError):
            ctx.search()

    def test_cost_report_without_catalog_raises(self):
        ctx = PermafrostContext()
        with pytest.raises(RuntimeError):
            ctx.cost_report()


# ── context manager ───────────────────────────────────────────────────────────

class TestContextManager:

    def test_context_manager_basic(self, sample_df, tmp):
        out = os.path.join(tmp, "cm.permafrost")
        with PermafrostContext() as ctx:
            ctx.freeze(sample_df, out)
        assert os.path.exists(out)

    def test_close_idempotent(self, tmp):
        ctx = PermafrostContext(catalog=os.path.join(tmp, "cat.db"))
        _ = ctx.catalog  # force open
        ctx.close()
        ctx.close()  # second close should not raise

    def test_context_manager_with_catalog(self, sample_df, tmp):
        db = os.path.join(tmp, "cm_cat.db")
        out = os.path.join(tmp, "cm2.permafrost")
        with PermafrostContext(catalog=db) as ctx:
            ctx.freeze(sample_df, out)
            results = ctx.search()
        assert len(results) >= 1


# ── cluster mock ─────────────────────────────────────────────────────────────

class TestClusterIntegration:

    def test_freeze_via_cluster(self, sample_df, tmp):
        mock_client = MagicMock()
        mock_client.freeze.return_value = "job-123"
        mock_client.wait.return_value = {"status": "done", "ratio": 8.5}

        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            ctx = PermafrostContext(cluster="http://master:8700")
            metrics = ctx.freeze(sample_df, "vendas_2024")

        assert metrics["ratio"] == 8.5
        mock_client.freeze.assert_called_once()

    def test_freeze_async_and_wait(self, sample_df, tmp):
        mock_client = MagicMock()
        mock_client.freeze.return_value = "job-456"
        mock_client.wait.return_value = {"status": "done", "ratio": 10.2}

        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            ctx = PermafrostContext(cluster="http://master:8700", token="jwt_token")
            job_id = ctx.freeze_async(sample_df, "async_test")
            result = ctx.wait(job_id)

        assert job_id == "job-456"
        assert result["ratio"] == 10.2

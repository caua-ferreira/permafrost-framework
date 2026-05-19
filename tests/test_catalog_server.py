"""
tests/test_catalog_server.py — Testes de integração para PermafrostCatalogServer.

Inicia o servidor em thread daemon via uvicorn e chama todos os endpoints
com httpx, verificando status codes, estrutura das respostas e comportamento
de erro (404, 400).
"""

import os
import shutil
import socket
import tempfile
import threading
import time

import httpx
import numpy as np
import pandas as pd
import pytest
import uvicorn

import permafrost as pf
from permafrost.catalog_server import PermafrostCatalogServer


# ── helpers ───────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_df(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "id":  np.arange(n, dtype=np.int64),
        "val": rng.random(n).astype(np.float32),
        "cat": np.where(np.arange(n) % 2 == 0, "even", "odd"),
    })


def _freeze_tmp(directory: str, filename: str = "test.permafrost", n: int = 200) -> str:
    path = os.path.join(directory, filename)
    pf.freeze(_make_df(n), path)
    return path


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def server_client(tmp_path_factory):
    """Sobe PermafrostCatalogServer em thread daemon; retorna (httpx.Client, tmp_dir)."""
    tmp = str(tmp_path_factory.mktemp("srv_data"))
    db_path = os.path.join(tmp, "test_catalog.db")

    port = _free_port()
    srv = PermafrostCatalogServer(db_path)

    thread = threading.Thread(
        target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="error"),
        daemon=True,
    )
    thread.start()
    time.sleep(1.5)

    base_url = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base_url, timeout=10)
    yield client, tmp
    client.close()


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealth:

    def test_health_returns_ok(self, server_client):
        client, _ = server_client
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_health_has_required_keys(self, server_client):
        client, _ = server_client
        data = client.get("/health").json()
        for key in ("status", "catalog_path", "total_datasets", "total_rows", "total_mb"):
            assert key in data, f"Missing key: {key}"

    def test_health_total_datasets_is_int(self, server_client):
        client, _ = server_client
        assert isinstance(client.get("/health").json()["total_datasets"], int)


# ── /datasets/register ────────────────────────────────────────────────────────

class TestRegister:

    def test_register_returns_registered(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("reg"))
        path = _freeze_tmp(tmp)
        r = client.post("/datasets/register", json={"path": path})
        assert r.status_code == 200
        assert r.json()["status"] == "registered"

    def test_register_with_name_and_version(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("reg_nv"))
        path = _freeze_tmp(tmp)
        r = client.post("/datasets/register", json={
            "path": path, "name": "my_dataset", "version": "v1.0",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "my_dataset"
        assert data["version"] == "v1.0"

    def test_register_already_registered(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("dup"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path})
        r = client.post("/datasets/register", json={"path": path})
        assert r.status_code == 200
        assert r.json()["status"] == "already_registered"

    def test_register_missing_file_returns_404(self, server_client):
        client, _ = server_client
        r = client.post("/datasets/register", json={"path": "/nonexistent/file.permafrost"})
        assert r.status_code == 404

    def test_register_with_tags(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("tags"))
        path = _freeze_tmp(tmp)
        r = client.post("/datasets/register", json={
            "path": path, "name": "tagged_ds", "tags": ["prod", "archive"],
        })
        assert r.status_code == 200
        assert r.json()["status"] == "registered"


# ── /datasets/register_dir ────────────────────────────────────────────────────

class TestRegisterDir:

    def test_register_dir_returns_list(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("dir"))
        _freeze_tmp(tmp, "a.permafrost")
        _freeze_tmp(tmp, "b.permafrost")
        r = client.post("/datasets/register_dir", json={"directory": tmp})
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) == 2

    def test_register_dir_missing_dir_returns_404(self, server_client):
        client, _ = server_client
        r = client.post("/datasets/register_dir", json={"directory": "/nonexistent/path"})
        assert r.status_code == 404


# ── /datasets (list/search) ───────────────────────────────────────────────────

class TestListDatasets:

    def test_list_returns_list(self, server_client):
        client, _ = server_client
        r = client.get("/datasets")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_filter_by_name(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("filter"))
        path = _freeze_tmp(tmp, "unique_xyz.permafrost")
        client.post("/datasets/register", json={"path": path, "name": "unique_xyz"})
        r = client.get("/datasets", params={"name": "unique_xyz"})
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert all("unique_xyz" in d["name"] for d in data)

    def test_list_filter_lossless_only(self, server_client):
        client, _ = server_client
        r = client.get("/datasets", params={"lossless_only": True})
        assert r.status_code == 200
        for d in r.json():
            assert d["quant"] == 0


# ── /datasets/{name} ─────────────────────────────────────────────────────────

class TestGetDataset:

    def test_get_existing_dataset(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("get"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "get_test"})
        r = client.get("/datasets/get_test")
        assert r.status_code == 200
        assert "name" in r.json()

    def test_get_nonexistent_returns_404(self, server_client):
        client, _ = server_client
        r = client.get("/datasets/does_not_exist_ever")
        assert r.status_code == 404


# ── /datasets/{name}/versions ─────────────────────────────────────────────────

class TestVersions:

    def test_versions_returns_list(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("ver"))
        p1 = _freeze_tmp(tmp, "v1.permafrost", n=100)
        p2 = _freeze_tmp(tmp, "v2.permafrost", n=50)
        client.post("/datasets/register", json={"path": p1, "name": "versioned_ds", "version": "v1"})
        client.post("/datasets/register", json={"path": p2, "name": "versioned_ds", "version": "v2"})
        r = client.get("/datasets/versioned_ds/versions")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 2
        versions = {d["version"] for d in data}
        assert {"v1", "v2"}.issubset(versions)

    def test_versions_nonexistent_returns_404(self, server_client):
        client, _ = server_client
        r = client.get("/datasets/no_such_dataset_xyz/versions")
        assert r.status_code == 404


# ── /datasets/{name}/chunks ───────────────────────────────────────────────────

class TestChunks:

    def test_chunks_returns_list(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("chunks"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "chunks_test"})
        r = client.get("/datasets/chunks_test/chunks")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_chunks_has_expected_fields(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("chk2"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "chk_fields"})
        data = client.get("/datasets/chk_fields/chunks").json()
        if data:
            row = data[0]
            for field in ("chunk_id", "row_start", "row_end", "byte_offset", "byte_len", "sha256"):
                assert field in row, f"Missing field: {field}"


# ── /datasets/{name}/integrity ────────────────────────────────────────────────

class TestIntegrity:

    def test_integrity_ok(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("ic"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "ic_test"})
        r = client.get("/datasets/ic_test/integrity")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert data[0]["status"] == "OK"

    def test_integrity_missing_file(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("ic2"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "ic_gone"})
        os.remove(path)
        r = client.get("/datasets/ic_gone/integrity")
        assert r.status_code == 200
        assert r.json()[0]["status"] == "FILE_MISSING"

    def test_integrity_nonexistent_returns_404(self, server_client):
        client, _ = server_client
        r = client.get("/datasets/zzz_no_such/integrity")
        assert r.status_code == 404


# ── DELETE /datasets/{name} ───────────────────────────────────────────────────

class TestDeleteDataset:

    def test_delete_removes_dataset(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("del"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "to_delete"})
        r = client.delete("/datasets/to_delete")
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"

        # Confirm gone
        r2 = client.get("/datasets/to_delete")
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self, server_client):
        client, _ = server_client
        r = client.delete("/datasets/ghost_dataset_xyz")
        assert r.status_code == 404


# ── /stats ────────────────────────────────────────────────────────────────────

class TestStats:

    def test_stats_returns_dict(self, server_client):
        client, _ = server_client
        r = client.get("/stats")
        assert r.status_code == 200
        data = r.json()
        assert "total_datasets" in data
        assert "total_rows" in data
        assert "total_mb" in data

    def test_stats_counts_increase_after_register(self, server_client, tmp_path_factory):
        client, _ = server_client
        before = client.get("/stats").json()["total_datasets"] or 0
        tmp = str(tmp_path_factory.mktemp("stat"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "stat_inc"})
        after = client.get("/stats").json()["total_datasets"] or 0
        assert after > before


# ── /cost_report ──────────────────────────────────────────────────────────────

class TestCostReport:

    def test_cost_report_default_tier(self, server_client):
        client, _ = server_client
        r = client.get("/cost_report")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_cost_report_has_cost_fields(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("cost"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "cost_ds"})
        data = client.get("/cost_report").json()
        if data:
            row = data[0]
            assert "cost_monthly_usd" in row
            assert "cost_annual_usd" in row

    def test_cost_report_all_tiers(self, server_client):
        client, _ = server_client
        for tier in ("s3_standard", "s3_ia", "glacier", "glacier_deep"):
            r = client.get("/cost_report", params={"tier": tier})
            assert r.status_code == 200, f"Failed for tier: {tier}"

    def test_cost_report_invalid_tier_returns_400(self, server_client):
        client, _ = server_client
        r = client.get("/cost_report", params={"tier": "fake_tier"})
        assert r.status_code == 400


# ── /sql ─────────────────────────────────────────────────────────────────────

class TestSql:

    def test_sql_select_all(self, server_client):
        client, _ = server_client
        r = client.post("/sql", json={"query": "SELECT COUNT(*) AS n FROM datasets"})
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert "n" in data[0]

    def test_sql_invalid_query_returns_400(self, server_client):
        client, _ = server_client
        r = client.post("/sql", json={"query": "SELECT * FROM nonexistent_table_xyz"})
        assert r.status_code == 400

    def test_sql_select_datasets_columns(self, server_client):
        client, _ = server_client
        r = client.post("/sql", json={"query": "SELECT name, codec FROM datasets LIMIT 5"})
        assert r.status_code == 200


# ── /search ───────────────────────────────────────────────────────────────────

class TestSearch:

    def test_search_returns_list(self, server_client):
        client, _ = server_client
        r = client.get("/search")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_search_filter_by_name(self, server_client, tmp_path_factory):
        client, _ = server_client
        tmp = str(tmp_path_factory.mktemp("srch"))
        path = _freeze_tmp(tmp)
        client.post("/datasets/register", json={"path": path, "name": "searchable_abc"})
        r = client.get("/search", params={"name": "searchable_abc"})
        assert r.status_code == 200
        assert len(r.json()) >= 1


# ── Public API export ─────────────────────────────────────────────────────────

class TestPublicAPIExport:

    def test_catalog_server_exported(self):
        assert hasattr(pf, "PermafrostCatalogServer")
        assert callable(pf.PermafrostCatalogServer)

    def test_catalog_server_has_app(self, tmp_path_factory):
        tmp = str(tmp_path_factory.mktemp("exp"))
        db_path = os.path.join(tmp, "exp.db")
        srv = pf.PermafrostCatalogServer(db_path)
        assert hasattr(srv, "app")
        assert hasattr(srv, "catalog")


if __name__ == "__main__":
    import unittest
    unittest.main()

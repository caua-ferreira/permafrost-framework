"""
In-process CLI tests using typer.testing.CliRunner — counted by coverage.
These cover the lines missed by subprocess-based tests in test_cli_cobertura.py.
"""
import os
import tempfile
import shutil
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from permafrost.cli import app
import permafrost as pf

runner = CliRunner()


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_csv(tmp):
    np.random.seed(0)
    N = 500
    df = pd.DataFrame({
        "id":     np.arange(1, N + 1, dtype=np.int32),
        "ano":    np.random.choice([2021, 2022, 2023], N).astype(np.int16),
        "total":  np.round(np.random.uniform(10, 999, N), 2),
        "status": np.random.choice(["Ativo", "Inativo"], N),
    })
    df = df.sort_values("ano").reset_index(drop=True)
    path = os.path.join(tmp, "input.csv")
    df.to_csv(path, index=False)
    return path, df


@pytest.fixture
def sample_pf(sample_csv, tmp):
    path, df = sample_csv
    pf_path = os.path.join(tmp, "data.permafrost")
    pf.freeze(df, pf_path, chunk_rows=100, partition_by="ano")
    return pf_path, df


# ── FREEZE ────────────────────────────────────────────────────────────────────

class TestFreezeInProcess:

    def test_freeze_csv_basic(self, sample_csv, tmp):
        csv_path, _ = sample_csv
        out = os.path.join(tmp, "out.permafrost")
        result = runner.invoke(app, ["freeze", csv_path, "--output", out])
        assert os.path.exists(out), f"exit={result.exit_code} output={result.output}"

    def test_freeze_zstd_codec(self, sample_csv, tmp):
        csv_path, _ = sample_csv
        out = os.path.join(tmp, "out_zstd.permafrost")
        result = runner.invoke(app, ["freeze", csv_path, "--output", out, "--codec", "zstd"])
        assert os.path.exists(out)
        info = pf.audit(out)
        assert info["codec"] == "zstd"

    def test_freeze_invalid_codec(self, sample_csv, tmp):
        csv_path, _ = sample_csv
        out = os.path.join(tmp, "fail.permafrost")
        result = runner.invoke(app, ["freeze", csv_path, "--output", out, "--codec", "badcodec"])
        assert result.exit_code != 0

    def test_freeze_invalid_quant(self, sample_csv, tmp):
        csv_path, _ = sample_csv
        out = os.path.join(tmp, "fail.permafrost")
        result = runner.invoke(app, ["freeze", csv_path, "--output", out, "--quant", "ultra"])
        assert result.exit_code != 0

    def test_freeze_file_not_found(self, tmp):
        out = os.path.join(tmp, "fail.permafrost")
        result = runner.invoke(app, ["freeze", "/not/exist.csv", "--output", out])
        assert result.exit_code != 0

    def test_freeze_with_partition(self, sample_csv, tmp):
        csv_path, _ = sample_csv
        out = os.path.join(tmp, "part.permafrost")
        result = runner.invoke(app, ["freeze", csv_path, "--output", out, "--partition-by", "ano"])
        assert os.path.exists(out)
        info = pf.audit(out)
        assert info["partition_col"] == "ano"

    def test_freeze_with_comment(self, sample_csv, tmp):
        csv_path, _ = sample_csv
        out = os.path.join(tmp, "commented.permafrost")
        result = runner.invoke(app, ["freeze", csv_path, "--output", out, "--comment", "test note"])
        assert os.path.exists(out)

    def test_freeze_header_unicode_fallback(self, sample_csv, tmp):
        csv_path, _ = sample_csv
        out = os.path.join(tmp, "uni.permafrost")
        with patch("permafrost.cli.console") as mock_console:
            mock_console.print.side_effect = [UnicodeEncodeError("utf-8", "", 0, 1, "test"), None, None, None, None, None, None, None, None, None]
            result = runner.invoke(app, ["freeze", csv_path, "--output", out])
        # Even if console threw on first print, file may still exist

    def test_header_unicodeencode_fallback_direct(self):
        """Directly test the _header() UnicodeEncodeError branch (lines 52-53)."""
        from permafrost.cli import _header
        with patch("permafrost.cli.console") as mock_console:
            mock_console.print.side_effect = [UnicodeEncodeError("utf-8", "x", 0, 1, "reason"), None]
            _header()
        # Second call (fallback print) should have been made
        assert mock_console.print.call_count >= 2


# ── THAW ──────────────────────────────────────────────────────────────────────

class TestThawInProcess:

    def test_thaw_basic(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        out = os.path.join(tmp, "thawed.csv")
        result = runner.invoke(app, ["thaw", pf_path, "--output", out])
        assert result.exit_code == 0
        assert os.path.exists(out)

    def test_thaw_not_found(self, tmp):
        result = runner.invoke(app, ["thaw", "/not/exist.permafrost"])
        assert result.exit_code != 0

    def test_thaw_with_filter(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        out = os.path.join(tmp, "filtered.csv")
        result = runner.invoke(app, [
            "thaw", pf_path, "--output", out,
            "--filter-col", "ano", "--filter-val", "2022",
        ])
        assert result.exit_code == 0
        assert os.path.exists(out)

    def test_thaw_parquet_output(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        out = os.path.join(tmp, "thawed.parquet")
        result = runner.invoke(app, ["thaw", pf_path, "--output", out])
        assert result.exit_code == 0
        assert os.path.exists(out)
        df_back = pd.read_parquet(out)
        assert len(df_back) > 0

    def test_thaw_no_verify(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        out = os.path.join(tmp, "no_verify.csv")
        result = runner.invoke(app, ["thaw", pf_path, "--output", out, "--no-verify"])
        assert result.exit_code == 0
        assert "pulada" in result.output or os.path.exists(out)


# ── AUDIT ─────────────────────────────────────────────────────────────────────

class TestAuditInProcess:

    def test_audit_basic(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        result = runner.invoke(app, ["audit", pf_path])
        assert result.exit_code == 0
        assert "lzma" in result.output.lower() or "zstd" in result.output.lower() or "Codec" in result.output

    def test_audit_not_found(self, tmp):
        result = runner.invoke(app, ["audit", "/not/exist.permafrost"])
        assert result.exit_code != 0

    def test_audit_with_chunks(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        result = runner.invoke(app, ["audit", pf_path, "--chunks"])
        assert result.exit_code == 0
        # Should show chunk table header
        assert "Chunk" in result.output or "chunk" in result.output.lower()


# ── VERIFY ────────────────────────────────────────────────────────────────────

class TestVerifyInProcess:

    def test_verify_good_file(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        result = runner.invoke(app, ["verify", pf_path])
        assert result.exit_code == 0

    def test_verify_not_found(self, tmp):
        result = runner.invoke(app, ["verify", "/not/exist.permafrost"])
        assert result.exit_code != 0

    def test_verify_corrupt_file(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        corrupt = os.path.join(tmp, "corrupt.permafrost")
        shutil.copy(pf_path, corrupt)
        sz = os.path.getsize(corrupt)
        with open(corrupt, "r+b") as f:
            f.seek(sz // 2)
            f.write(b"\xFF" * 64)
        result = runner.invoke(app, ["verify", corrupt])
        assert result.exit_code != 0


# ── CATALOG REGISTER ──────────────────────────────────────────────────────────

class TestCatalogRegisterInProcess:

    def test_catalog_register_file(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        db = os.path.join(tmp, "cat.db")
        result = runner.invoke(app, ["catalog", "register", pf_path, "--db", db])
        assert result.exit_code == 0
        assert "registrado" in result.output.lower() or "já registrado" in result.output.lower()

    def test_catalog_register_already_registered(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        db = os.path.join(tmp, "cat2.db")
        runner.invoke(app, ["catalog", "register", pf_path, "--db", db])
        result = runner.invoke(app, ["catalog", "register", pf_path, "--db", db])
        assert result.exit_code == 0

    def test_catalog_register_dir(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        pf_dir = os.path.dirname(pf_path)
        db = os.path.join(tmp, "cat_dir.db")
        result = runner.invoke(app, ["catalog", "register", pf_dir, "--db", db])
        assert result.exit_code == 0
        assert "registrado" in result.output.lower() or "arquivo" in result.output.lower()

    def test_catalog_register_with_tags(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        db = os.path.join(tmp, "cat_tags.db")
        result = runner.invoke(app, ["catalog", "register", pf_path, "--db", db, "--tags", "prod,sales"])
        assert result.exit_code == 0


# ── CATALOG SEARCH ────────────────────────────────────────────────────────────

class TestCatalogSearchInProcess:

    @pytest.fixture
    def populated_db(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        db = os.path.join(tmp, "search.db")
        runner.invoke(app, ["catalog", "register", pf_path, "--db", db])
        return db

    def test_search_empty_results(self, populated_db, tmp):
        result = runner.invoke(app, ["catalog", "search", "--db", populated_db, "--name", "xxxxxxxx"])
        assert result.exit_code == 0
        assert "nenhum" in result.output.lower() or "não encontrado" in result.output.lower()

    def test_search_with_results(self, populated_db, tmp):
        result = runner.invoke(app, ["catalog", "search", "--db", populated_db])
        assert result.exit_code == 0
        assert "data" in result.output.lower() or "resultado" in result.output.lower() or "Nome" in result.output

    def test_search_by_name(self, populated_db, tmp):
        result = runner.invoke(app, ["catalog", "search", "--db", populated_db, "--name", "data"])
        assert result.exit_code == 0

    def test_search_lossless_only(self, populated_db, tmp):
        result = runner.invoke(app, ["catalog", "search", "--db", populated_db, "--lossless"])
        assert result.exit_code == 0


# ── CATALOG COST ──────────────────────────────────────────────────────────────

class TestCatalogCostInProcess:

    @pytest.fixture
    def populated_db(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        db = os.path.join(tmp, "cost.db")
        runner.invoke(app, ["catalog", "register", pf_path, "--db", db])
        return db

    def test_cost_glacier_deep(self, populated_db):
        result = runner.invoke(app, ["catalog", "cost", "--db", populated_db, "--tier", "glacier-deep"])
        assert result.exit_code == 0
        assert "$" in result.output or "Total" in result.output

    def test_cost_s3(self, populated_db):
        result = runner.invoke(app, ["catalog", "cost", "--db", populated_db, "--tier", "s3"])
        assert result.exit_code == 0

    def test_cost_glacier(self, populated_db):
        result = runner.invoke(app, ["catalog", "cost", "--db", populated_db, "--tier", "glacier"])
        assert result.exit_code == 0


# ── CATALOG VERIFY ────────────────────────────────────────────────────────────

class TestCatalogVerifyInProcess:

    @pytest.fixture
    def populated_db(self, sample_pf, tmp):
        pf_path, _ = sample_pf
        db = os.path.join(tmp, "integ.db")
        runner.invoke(app, ["catalog", "register", pf_path, "--db", db])
        return db

    def test_catalog_verify_all_ok(self, populated_db):
        result = runner.invoke(app, ["catalog", "verify", "--db", populated_db])
        assert result.exit_code == 0
        assert "ok" in result.output.lower() or "íntegro" in result.output.lower() or "✓" in result.output

    def test_catalog_verify_name_filter(self, populated_db):
        result = runner.invoke(app, ["catalog", "verify", "--db", populated_db, "--name", "data"])
        assert result.exit_code == 0


# ── CLUSTER ADD-USER ──────────────────────────────────────────────────────────

class TestClusterUsersInProcess:

    def _mock_client(self):
        mock_client = MagicMock()
        mock_client.add_user.return_value = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.token"
        mock_client.list_users.return_value = [
            {"username": "alice", "can_freeze": True, "can_thaw": False, "namespace": "prod"},
            {"username": "bob",   "can_freeze": False, "can_thaw": True,  "namespace": "default"},
        ]
        mock_client.remove_user.return_value = {"existed": True}
        return mock_client

    def test_add_user_success(self):
        mock_client = self._mock_client()
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "add-user", "alice",
                "--can-freeze",
                "--namespace", "prod",
                "--admin-key", "secret",
            ])
        assert result.exit_code == 0
        assert "alice" in result.output

    def test_add_user_with_thaw(self):
        mock_client = self._mock_client()
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "add-user", "bob",
                "--can-thaw",
                "--admin-key", "secret",
            ])
        assert result.exit_code == 0
        assert "bob" in result.output

    def test_add_user_no_permissions(self):
        mock_client = self._mock_client()
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "add-user", "carol",
                "--admin-key", "secret",
            ])
        assert result.exit_code == 0
        assert "nenhuma" in result.output

    def test_add_user_error(self):
        mock_client = MagicMock()
        mock_client.add_user.side_effect = Exception("Connection refused")
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "add-user", "dave",
                "--admin-key", "secret",
            ])
        assert result.exit_code != 0

    def test_list_users_success(self):
        mock_client = self._mock_client()
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "list-users",
                "--admin-key", "secret",
            ])
        assert result.exit_code == 0
        assert "alice" in result.output
        assert "bob" in result.output

    def test_list_users_empty(self):
        mock_client = MagicMock()
        mock_client.list_users.return_value = []
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "list-users",
                "--admin-key", "secret",
            ])
        assert result.exit_code == 0
        assert "nenhum" in result.output.lower()

    def test_list_users_error(self):
        mock_client = MagicMock()
        mock_client.list_users.side_effect = Exception("timeout")
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "list-users",
                "--admin-key", "secret",
            ])
        assert result.exit_code != 0

    def test_remove_user_success(self):
        mock_client = self._mock_client()
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "remove-user", "alice",
                "--admin-key", "secret",
            ])
        assert result.exit_code == 0
        assert "alice" in result.output

    def test_remove_user_not_found(self):
        mock_client = MagicMock()
        mock_client.remove_user.return_value = {"existed": False}
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "remove-user", "ghost",
                "--admin-key", "secret",
            ])
        assert result.exit_code == 0
        assert "ghost" in result.output

    def test_remove_user_error(self):
        mock_client = MagicMock()
        mock_client.remove_user.side_effect = Exception("network error")
        with patch("permafrost.cluster.PermafrostClient", return_value=mock_client):
            result = runner.invoke(app, [
                "cluster", "remove-user", "alice",
                "--admin-key", "secret",
            ])
        assert result.exit_code != 0

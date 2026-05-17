"""Tests for I3 — CLI entry point and standalone binary readiness.

Validates that the CLI entry point works correctly so the PyInstaller
binary (built from permafrost.spec) will behave the same way.
"""
import json
import os
import subprocess
import sys
import tempfile

import pandas as pd
import pytest
from typer.testing import CliRunner

from permafrost.cli import app

runner = CliRunner()


# ── entry point integrity ─────────────────────────────────────────────────────

class TestEntryPoint:
    def test_app_is_typer(self):
        import typer
        assert isinstance(app, typer.Typer)

    def test_help_exits_zero(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_help_mentions_freeze(self):
        result = runner.invoke(app, ["--help"])
        assert "freeze" in result.output.lower()

    def test_help_mentions_thaw(self):
        result = runner.invoke(app, ["--help"])
        assert "unfreeze" in result.output.lower()

    def test_help_mentions_audit(self):
        result = runner.invoke(app, ["--help"])
        assert "audit" in result.output.lower()

    def test_help_mentions_catalog(self):
        result = runner.invoke(app, ["--help"])
        assert "catalog" in result.output.lower()

    def test_freeze_help_exits_zero(self):
        result = runner.invoke(app, ["freeze", "--help"])
        assert result.exit_code == 0

    def test_thaw_help_exits_zero(self):
        result = runner.invoke(app, ["unfreeze", "--help"])
        assert result.exit_code == 0

    def test_audit_help_exits_zero(self):
        result = runner.invoke(app, ["audit", "--help"])
        assert result.exit_code == 0

    def test_verify_help_exits_zero(self):
        result = runner.invoke(app, ["verify", "--help"])
        assert result.exit_code == 0

    def test_catalog_help_exits_zero(self):
        result = runner.invoke(app, ["catalog", "--help"])
        assert result.exit_code == 0

    def test_catalog_register_help(self):
        result = runner.invoke(app, ["catalog", "register", "--help"])
        assert result.exit_code == 0

    def test_catalog_search_help(self):
        result = runner.invoke(app, ["catalog", "search", "--help"])
        assert result.exit_code == 0

    def test_catalog_cost_help(self):
        result = runner.invoke(app, ["catalog", "cost", "--help"])
        assert result.exit_code == 0

    def test_cluster_help(self):
        result = runner.invoke(app, ["cluster", "--help"])
        assert result.exit_code == 0

    def test_cluster_add_user_help(self):
        result = runner.invoke(app, ["cluster", "add-user", "--help"])
        assert result.exit_code == 0

    def test_cluster_list_users_help(self):
        result = runner.invoke(app, ["cluster", "list-users", "--help"])
        assert result.exit_code == 0

    def test_cluster_remove_user_help(self):
        result = runner.invoke(app, ["cluster", "remove-user", "--help"])
        assert result.exit_code == 0


# ── freeze command ────────────────────────────────────────────────────────────

class TestFreezeCommand:
    def _make_csv(self, n=100):
        tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w")
        tmp.write("id,name,value,year\n")
        for i in range(n):
            tmp.write(f"{i},item_{i%10},{i*1.5},{2020+i%5}\n")
        tmp.close()
        return tmp.name

    def test_freeze_csv(self):
        csv_path = self._make_csv()
        out_path = csv_path.replace(".csv", ".permafrost")
        try:
            result = runner.invoke(app, ["freeze", csv_path, "--output", out_path])
            assert result.exit_code == 0, result.output
            assert os.path.exists(out_path)
        finally:
            os.unlink(csv_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_freeze_missing_input(self):
        result = runner.invoke(app, ["freeze", "nonexistent.csv"])
        assert result.exit_code != 0

    def test_freeze_invalid_codec(self):
        csv_path = self._make_csv()
        try:
            result = runner.invoke(app, ["freeze", csv_path, "--codec", "invalid"])
            assert result.exit_code != 0
        finally:
            os.unlink(csv_path)

    def test_freeze_invalid_quant(self):
        csv_path = self._make_csv()
        try:
            result = runner.invoke(app, ["freeze", csv_path, "--quant", "invalid"])
            assert result.exit_code != 0
        finally:
            os.unlink(csv_path)

    def test_freeze_zstd_codec(self):
        csv_path = self._make_csv()
        out_path = csv_path.replace(".csv", ".permafrost")
        try:
            result = runner.invoke(app, ["freeze", csv_path, "--output", out_path, "--codec", "zstd"])
            assert result.exit_code == 0, result.output
        finally:
            os.unlink(csv_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_freeze_with_partition(self):
        csv_path = self._make_csv()
        out_path = csv_path.replace(".csv", ".permafrost")
        try:
            result = runner.invoke(app, ["freeze", csv_path, "--output", out_path,
                                         "--partition-by", "year"])
            assert result.exit_code == 0, result.output
        finally:
            os.unlink(csv_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_freeze_output_shows_ratio(self):
        csv_path = self._make_csv(500)
        out_path = csv_path.replace(".csv", ".permafrost")
        try:
            result = runner.invoke(app, ["freeze", csv_path, "--output", out_path])
            assert result.exit_code == 0
            assert "ratio" in result.output.lower() or "×" in result.output
        finally:
            os.unlink(csv_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


# ── thaw command ──────────────────────────────────────────────────────────────

class TestThawCommand:
    def _make_permafrost(self, n=100):
        import pandas as pd
        from permafrost.codec import freeze as pf_freeze, CODEC_ZSTD
        df = pd.DataFrame({"id": range(n), "value": [i * 1.5 for i in range(n)]})
        tmp = tempfile.NamedTemporaryFile(suffix=".permafrost", delete=False)
        tmp.close()
        pf_freeze(df, tmp.name, codec=CODEC_ZSTD)
        return tmp.name, df

    def test_thaw_to_csv(self):
        pf_path, orig = self._make_permafrost()
        out_csv = pf_path.replace(".permafrost", "_out.csv")
        try:
            result = runner.invoke(app, ["unfreeze", pf_path, "--output", out_csv])
            assert result.exit_code == 0, result.output
            assert os.path.exists(out_csv)
            df2 = pd.read_csv(out_csv)
            assert len(df2) == len(orig)
        finally:
            os.unlink(pf_path)
            if os.path.exists(out_csv):
                os.unlink(out_csv)

    def test_thaw_missing_input(self):
        result = runner.invoke(app, ["unfreeze", "nonexistent.permafrost"])
        assert result.exit_code != 0

    def test_thaw_shows_rows(self):
        pf_path, orig = self._make_permafrost(50)
        out_csv = pf_path.replace(".permafrost", "_out.csv")
        try:
            result = runner.invoke(app, ["unfreeze", pf_path, "--output", out_csv])
            assert result.exit_code == 0
            assert "50" in result.output
        finally:
            os.unlink(pf_path)
            if os.path.exists(out_csv):
                os.unlink(out_csv)


# ── audit command ─────────────────────────────────────────────────────────────

class TestAuditCommand:
    def _make_permafrost(self):
        import pandas as pd
        from permafrost.codec import freeze as pf_freeze, CODEC_ZSTD
        df = pd.DataFrame({"id": range(50), "x": range(50)})
        tmp = tempfile.NamedTemporaryFile(suffix=".permafrost", delete=False)
        tmp.close()
        pf_freeze(df, tmp.name, codec=CODEC_ZSTD)
        return tmp.name

    def test_audit_exits_zero(self):
        pf_path = self._make_permafrost()
        try:
            result = runner.invoke(app, ["audit", pf_path])
            assert result.exit_code == 0, result.output
        finally:
            os.unlink(pf_path)

    def test_audit_shows_codec(self):
        pf_path = self._make_permafrost()
        try:
            result = runner.invoke(app, ["audit", pf_path])
            assert "zstd" in result.output.lower()
        finally:
            os.unlink(pf_path)

    def test_audit_shows_rows(self):
        pf_path = self._make_permafrost()
        try:
            result = runner.invoke(app, ["audit", pf_path])
            assert "50" in result.output
        finally:
            os.unlink(pf_path)

    def test_audit_missing_input(self):
        result = runner.invoke(app, ["audit", "nonexistent.permafrost"])
        assert result.exit_code != 0

    def test_audit_chunks_flag(self):
        pf_path = self._make_permafrost()
        try:
            result = runner.invoke(app, ["audit", pf_path, "--chunks"])
            assert result.exit_code == 0
            # should show chunk details
            assert "sha256" in result.output.lower() or "chunk" in result.output.lower()
        finally:
            os.unlink(pf_path)


# ── verify command ────────────────────────────────────────────────────────────

class TestVerifyCommand:
    def _make_permafrost(self):
        import pandas as pd
        from permafrost.codec import freeze as pf_freeze, CODEC_ZSTD
        df = pd.DataFrame({"id": range(20)})
        tmp = tempfile.NamedTemporaryFile(suffix=".permafrost", delete=False)
        tmp.close()
        pf_freeze(df, tmp.name, codec=CODEC_ZSTD)
        return tmp.name

    def test_verify_intact_file(self):
        pf_path = self._make_permafrost()
        try:
            result = runner.invoke(app, ["verify", pf_path])
            assert result.exit_code == 0, result.output
        finally:
            os.unlink(pf_path)

    def test_verify_missing_file(self):
        result = runner.invoke(app, ["verify", "ghost.permafrost"])
        assert result.exit_code != 0

    def test_verify_corrupted_file(self):
        pf_path = self._make_permafrost()
        try:
            # Corrupt the file
            with open(pf_path, "r+b") as f:
                f.seek(100)
                f.write(b"\x00" * 50)
            result = runner.invoke(app, ["verify", pf_path])
            assert result.exit_code != 0
        finally:
            os.unlink(pf_path)


# ── module entry point ────────────────────────────────────────────────────────

class TestModuleEntryPoint:
    _env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    def test_cli_runnable_as_module(self):
        result = subprocess.run(
            [sys.executable, "-m", "permafrost.cli", "--help"],
            capture_output=True, text=True, encoding="utf-8",
            env=self._env,
        )
        assert result.returncode == 0
        assert "freeze" in result.stdout.lower()

    def test_freeze_via_subprocess(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("id,val\n")
            for i in range(20):
                f.write(f"{i},{i*2}\n")
            csv_path = f.name
        out_path = csv_path.replace(".csv", ".permafrost")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "permafrost.cli", "freeze", csv_path,
                 "--output", out_path, "--codec", "zstd"],
                capture_output=True, text=True, encoding="utf-8",
                env=self._env,
            )
            assert result.returncode == 0, result.stderr
            assert os.path.exists(out_path)
        finally:
            os.unlink(csv_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

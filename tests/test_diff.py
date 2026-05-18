"""
Testes para pf.diff()
=====================
Executar: pytest tests/test_diff.py -v
"""
import os
import shutil
import tempfile
import warnings

import numpy as np
import pandas as pd
import pytest

import permafrost as pf


# ─────────────────────────── fixtures ────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(scope="module")
def base_df():
    np.random.seed(7)
    N = 300
    return pd.DataFrame({
        "id":     np.arange(1, N + 1, dtype=np.int32),
        "nome":   [f"Cliente_{i}" for i in range(1, N + 1)],
        "regiao": np.random.choice(["Norte", "Sul", "Leste", "Oeste"], N),
        "valor":  np.round(np.random.uniform(10, 5000, N), 2),
        "ano":    np.random.choice([2021, 2022, 2023], N).astype(np.int16),
    })


# ─────────────── arquivos idênticos ──────────────────────────────────────────

class TestDiffIdentical:
    def test_identical_files_zero_diff(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        result = pf.diff(p1, p1)
        assert result["summary"]["inserted"]  == 0
        assert result["summary"]["deleted"]   == 0
        assert result["summary"]["changed"]   == 0
        assert result["summary"]["unchanged"] == len(base_df)

    def test_identical_summary_only(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        s = pf.diff(p1, p1, output="summary")
        assert s["changed"] == 0
        assert s["unchanged"] == len(base_df)

    def test_identical_dataframe_output_empty(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        df = pf.diff(p1, p1, output="dataframe")
        assert isinstance(df, pd.DataFrame)
        # _diff column present; no rows since nothing changed
        assert "_diff" in df.columns
        assert len(df) == 0


# ─────────────── linhas inseridas ────────────────────────────────────────────

class TestDiffInserted:
    def test_detects_inserted_rows(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        extra = pd.DataFrame({
            "id": [9001, 9002, 9003],
            "nome": ["A", "B", "C"],
            "regiao": ["Norte", "Sul", "Leste"],
            "valor": [100.0, 200.0, 300.0],
            "ano": [2024, 2024, 2024],
        })
        df_v2 = pd.concat([base_df, extra], ignore_index=True)
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2)
        assert result["summary"]["inserted"] == 3
        assert result["summary"]["deleted"]  == 0
        assert set(result["inserted"]["id"].tolist()) == {9001, 9002, 9003}

    def test_inserted_only_include(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        extra = base_df.copy()
        extra["id"] = extra["id"] + 10000
        df_v2 = pd.concat([base_df, extra], ignore_index=True)
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2, include=["inserted"])
        assert result["inserted"] is not None
        assert result["deleted"] is None
        assert result["changed"] is None


# ─────────────── linhas removidas ────────────────────────────────────────────

class TestDiffDeleted:
    def test_detects_deleted_rows(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        df_v2 = base_df[base_df["id"] > 10].copy()
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2)
        assert result["summary"]["deleted"] == 10
        assert set(result["deleted"]["id"].tolist()) == set(range(1, 11))

    def test_deleted_has_correct_columns(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        df_v2 = base_df.iloc[5:].copy()
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2)
        assert set(base_df.columns).issubset(set(result["deleted"].columns))


# ─────────────── linhas alteradas ────────────────────────────────────────────

class TestDiffChanged:
    def test_detects_changed_values(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        df_v2 = base_df.copy()
        df_v2.loc[df_v2["id"] <= 5, "valor"] = 99999.0
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2)
        assert result["summary"]["changed"] == 5

    def test_changed_has_v1_and_v2_columns(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        df_v2 = base_df.copy()
        df_v2.loc[df_v2["id"] == 1, "valor"] = 0.0
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2)
        ch = result["changed"]
        assert "valor_v1" in ch.columns
        assert "valor_v2" in ch.columns

    def test_changed_columns_only(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        df_v2 = base_df.copy()
        # Only change "valor" — nome/regiao/ano unchanged
        df_v2.loc[df_v2["id"] <= 3, "valor"] = 0.0
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2, changed_columns_only=True)
        ch = result["changed"]
        # valor_v1 and valor_v2 should be present
        assert "valor_v1" in ch.columns
        assert "valor_v2" in ch.columns
        # nome (unchanged) should NOT be present as nome_v1/nome_v2
        assert "nome_v1" not in ch.columns
        assert "nome_v2" not in ch.columns

    def test_float_tolerance_no_false_positive(self, tmp_dir):
        """Tiny float differences within rtol should NOT be reported as changed."""
        df1 = pd.DataFrame({"id": [1, 2], "v": [1.0, 2.0]})
        df2 = pd.DataFrame({"id": [1, 2], "v": [1.0 + 1e-12, 2.0 + 1e-12]})
        p1 = os.path.join(tmp_dir, "f1.permafrost")
        p2 = os.path.join(tmp_dir, "f2.permafrost")
        pf.freeze(df1, p1, primary_key="id")
        pf.freeze(df2, p2, primary_key="id")
        result = pf.diff(p1, p2)
        assert result["summary"]["changed"] == 0


# ─────────────── output formats ──────────────────────────────────────────────

class TestDiffOutputFormats:
    def test_dict_output_structure(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        result = pf.diff(p1, p1, output="dict")
        assert "inserted" in result
        assert "deleted" in result
        assert "changed" in result
        assert "unchanged_count" in result
        assert "summary" in result

    def test_summary_output_is_dict(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        s = pf.diff(p1, p1, output="summary")
        assert isinstance(s, dict)
        assert set(s.keys()) >= {"inserted", "deleted", "changed", "unchanged"}

    def test_dataframe_output_has_diff_col(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        extra = pd.DataFrame({
            "id": [9999], "nome": ["X"], "regiao": ["Sul"],
            "valor": [1.0], "ano": [2025],
        })
        pf.freeze(pd.concat([base_df, extra], ignore_index=True), p2, primary_key="id")
        df = pf.diff(p1, p2, output="dataframe")
        assert "_diff" in df.columns
        assert "inserted" in df["_diff"].values

    def test_invalid_output_raises(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        with pytest.raises(ValueError, match="output"):
            pf.diff(p1, p1, output="xml")


# ─────────────── chave primária ──────────────────────────────────────────────

class TestDiffPrimaryKey:
    def test_uses_stored_primary_key(self, base_df, tmp_dir):
        """Se primary_key foi gravado no arquivo, usa automaticamente."""
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        df_v2 = base_df.copy()
        df_v2.loc[df_v2["id"] == 1, "valor"] = 0.0
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2)  # sem on= — usa __pk__
        assert result["summary"]["changed"] == 1

    def test_on_param_overrides_stored_key(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1)  # sem primary_key no arquivo
        df_v2 = base_df.copy()
        df_v2.loc[df_v2["id"] == 1, "valor"] = 0.0
        pf.freeze(df_v2, p2)
        result = pf.diff(p1, p2, on="id")
        assert result["summary"]["changed"] == 1

    def test_missing_key_col_raises(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        pf.freeze(base_df, p1)
        with pytest.raises(ValueError, match="'inexistente'"):
            pf.diff(p1, p1, on="inexistente")

    def test_no_key_warns_and_uses_positional(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        pf.freeze(base_df, p1)  # sem primary_key
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            pf.diff(p1, p1, output="summary")
            assert any("posicional" in str(warning.message) for warning in w)


# ─────────────── schema incompatível ─────────────────────────────────────────

class TestDiffSchemaErrors:
    def test_extra_column_raises(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        df_bad = base_df.copy()
        df_bad["nova"] = 0
        pf.freeze(df_bad, p2)
        with pytest.raises(ValueError, match="[Ss]chema"):
            pf.diff(p1, p2)

    def test_missing_column_raises(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, primary_key="id")
        pf.freeze(base_df.drop(columns=["regiao"]), p2)
        with pytest.raises(ValueError, match="[Ss]chema"):
            pf.diff(p1, p2)


# ─────────────── .pf extension e particionamento ─────────────────────────────

class TestDiffMisc:
    def test_works_with_pf_extension(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.pf")
        p2 = os.path.join(tmp_dir, "v2.pf")
        pf.freeze(base_df, p1, primary_key="id")
        df_v2 = base_df.copy()
        df_v2.loc[0, "valor"] = 0.0
        pf.freeze(df_v2, p2, primary_key="id")
        result = pf.diff(p1, p2)
        assert result["summary"]["changed"] == 1

    def test_works_with_partitioned_files(self, base_df, tmp_dir):
        p1 = os.path.join(tmp_dir, "v1.permafrost")
        p2 = os.path.join(tmp_dir, "v2.permafrost")
        pf.freeze(base_df, p1, partition_by="ano", primary_key="id")
        df_v2 = base_df.copy()
        df_v2.loc[df_v2["id"] <= 5, "valor"] = 1.0
        pf.freeze(df_v2, p2, partition_by="ano", primary_key="id")
        result = pf.diff(p1, p2)
        assert result["summary"]["changed"] == 5
        assert result["summary"]["deleted"] == 0
        assert result["summary"]["inserted"] == 0


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v", "--tb=short"])

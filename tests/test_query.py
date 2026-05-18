"""
Testes para as novas features:
- primary_key em freeze() / audit()
- extensao .pf alias
- pf.query() — SQL direto e via alias register
- output_format em unfreeze()

Executar: pytest tests/test_query.py -v
"""
import os
import shutil
import tempfile
import json

import numpy as np
import pandas as pd
import pytest

import permafrost as pf
from permafrost.query import (
    register, unregister, registered,
    _find_file_refs, _extract_simple_filters,
)


# ─────────────────────────── fixtures ────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(scope="module")
def clientes_df():
    np.random.seed(1)
    N = 500
    return pd.DataFrame({
        "id":     np.arange(1, N + 1, dtype=np.int32),
        "nome":   [f"Cliente_{i}" for i in range(1, N + 1)],
        "regiao": np.random.choice(["Norte", "Sul", "Leste", "Oeste"], N),
        "ano":    np.random.choice([2021, 2022, 2023], N).astype(np.int16),
        "score":  np.round(np.random.uniform(0, 100, N), 2),
    })


@pytest.fixture(scope="module")
def pedidos_df(clientes_df):
    np.random.seed(2)
    N = 1000
    return pd.DataFrame({
        "pedido_id":  np.arange(1, N + 1, dtype=np.int32),
        "cliente_id": np.random.choice(clientes_df["id"], N).astype(np.int32),
        "valor":      np.round(np.random.uniform(10, 5000, N), 2),
        "ano":        np.random.choice([2021, 2022, 2023], N).astype(np.int16),
    })


# ─────────────── primary_key ──────────────────────────────────────────────────

class TestPrimaryKey:
    def test_primary_key_stored_in_audit(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path, primary_key="id")
        info = pf.audit(path)
        assert info["primary_key"] == ["id"]

    def test_primary_key_composite(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path, primary_key=["ano", "id"])
        info = pf.audit(path)
        assert info["primary_key"] == ["ano", "id"]

    def test_primary_key_in_freeze_return(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        m = pf.freeze(clientes_df, path, primary_key="id")
        assert m["primary_key"] == ["id"]

    def test_no_primary_key_returns_none(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        info = pf.audit(path)
        assert info["primary_key"] is None

    def test_primary_key_does_not_appear_as_column(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path, primary_key="id")
        df = pf.unfreeze(path)
        assert "__pk__" not in df.columns
        assert set(df.columns) == set(clientes_df.columns)

    def test_roundtrip_with_primary_key(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path, primary_key="id")
        df = pf.unfreeze(path)
        assert len(df) == len(clientes_df)
        assert set(df.columns) == set(clientes_df.columns)

    def test_primary_key_old_file_backward_compat(self, clientes_df, tmp_dir):
        """Files without __pk__ must still load and return primary_key=None."""
        path = os.path.join(tmp_dir, "old.permafrost")
        pf.freeze(clientes_df, path)
        info = pf.audit(path)
        assert info.get("primary_key") is None


# ─────────────── .pf extension ───────────────────────────────────────────────

class TestPfExtension:
    def test_freeze_to_pf_extension(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.pf")
        pf.freeze(clientes_df, path)
        assert os.path.exists(path)

    def test_unfreeze_from_pf(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.pf")
        pf.freeze(clientes_df, path)
        df = pf.unfreeze(path)
        assert len(df) == len(clientes_df)

    def test_audit_pf(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.pf")
        pf.freeze(clientes_df, path)
        info = pf.audit(path)
        assert info["orig_rows"] == len(clientes_df)

    def test_peek_pf(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.pf")
        pf.freeze(clientes_df, path, partition_by="ano")
        frames = list(pf.peek(path, filter={"ano": 2022}))
        result = pd.concat(frames, ignore_index=True)
        assert set(result["ano"].unique()) == {2022}

    def test_pf_extension_constant_exported(self):
        assert ".pf" in pf.PERMAFROST_EXTENSIONS
        assert ".permafrost" in pf.PERMAFROST_EXTENSIONS


# ─────────────── output_format in unfreeze() ─────────────────────────────────

class TestOutputFormat:
    def test_default_is_dataframe(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path)
        assert isinstance(result, pd.DataFrame)

    def test_records_format(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path, output_format="records")
        assert isinstance(result, list)
        assert isinstance(result[0], dict)
        assert len(result) == len(clientes_df)

    def test_json_format(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path, output_format="json")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == len(clientes_df)

    def test_csv_format(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path, output_format="csv")
        assert isinstance(result, str)
        assert "id" in result.split("\n")[0]

    def test_parquet_format(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path, output_format="parquet")
        assert isinstance(result, bytes)
        import io
        df2 = pd.read_parquet(io.BytesIO(result))
        assert len(df2) == len(clientes_df)

    def test_invalid_format_raises(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        with pytest.raises(ValueError, match="output_format"):
            pf.unfreeze(path, output_format="xml")

    # ── CSV com separador customizado ─────────────────────────────────────────

    def test_csv_default_comma_separator(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path, output_format="csv")
        header = result.split("\n")[0]
        assert "," in header

    def test_csv_semicolon_separator(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path, output_format="csv", sep=";")
        header = result.split("\n")[0]
        assert ";" in header
        assert "," not in header

    def test_csv_pipe_separator(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path, output_format="csv", sep="|")
        header = result.split("\n")[0]
        assert "|" in header

    def test_csv_header_contains_column_names(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        for sep in [",", ";", "|"]:
            result = pf.unfreeze(path, output_format="csv", sep=sep)
            header = result.split("\n")[0]
            for col in clientes_df.columns:
                assert col in header

    def test_csv_roundtrip_semicolon(self, clientes_df, tmp_dir):
        import io
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        csv_str = pf.unfreeze(path, output_format="csv", sep=";")
        df2 = pd.read_csv(io.StringIO(csv_str), sep=";")
        assert len(df2) == len(clientes_df)
        assert set(df2.columns) == set(clientes_df.columns)

    # ── XLSX ──────────────────────────────────────────────────────────────────

    def test_xlsx_format_returns_bytes(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        result = pf.unfreeze(path, output_format="xlsx")
        assert isinstance(result, bytes)
        assert result[:2] == b'PK'

    def test_xlsx_roundtrip(self, clientes_df, tmp_dir):
        import io
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        xlsx_bytes = pf.unfreeze(path, output_format="xlsx")
        df2 = pd.read_excel(io.BytesIO(xlsx_bytes))
        assert len(df2) == len(clientes_df)
        assert set(df2.columns) == set(clientes_df.columns)

    def test_xlsx_has_header(self, clientes_df, tmp_dir):
        import io
        path = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(clientes_df, path)
        xlsx_bytes = pf.unfreeze(path, output_format="xlsx")
        df2 = pd.read_excel(io.BytesIO(xlsx_bytes))
        for col in clientes_df.columns:
            assert col in df2.columns


# ─────────────── query() — unit helpers ──────────────────────────────────────

class TestQueryHelpers:
    def test_find_file_refs_permafrost(self):
        sql = "SELECT * FROM 'clientes.permafrost' c"
        refs = _find_file_refs(sql)
        assert "clientes.permafrost" in refs

    def test_find_file_refs_pf(self):
        sql = "SELECT * FROM 'pedidos.pf' p"
        refs = _find_file_refs(sql)
        assert "pedidos.pf" in refs

    def test_find_file_refs_multiple(self):
        sql = "SELECT * FROM 'a.permafrost' a JOIN 'b.pf' b ON a.id = b.id"
        refs = _find_file_refs(sql)
        assert len(refs) == 2

    def test_extract_simple_filters_equality(self):
        sql = "SELECT * FROM 'c.permafrost' c WHERE c.ano = 2022"
        f = _extract_simple_filters(sql, "c")
        assert f.get("ano") == 2022

    def test_extract_simple_filters_string(self):
        sql = "SELECT * FROM 'c.permafrost' c WHERE c.regiao = 'Sul'"
        f = _extract_simple_filters(sql, "c")
        assert f.get("regiao") == "Sul"

    def test_extract_simple_filters_in_list(self):
        sql = "SELECT * FROM 'c.permafrost' c WHERE c.ano IN (2021, 2022)"
        f = _extract_simple_filters(sql, "c")
        assert set(f.get("ano", [])) == {2021, 2022}

    def test_extract_simple_filters_between(self):
        sql = "SELECT * FROM 'c.permafrost' c WHERE c.ano BETWEEN 2021 AND 2023"
        f = _extract_simple_filters(sql, "c")
        assert f.get("ano") == (2021, 2023)

    def test_extract_no_alias_no_filter(self):
        sql = "SELECT * FROM 'c.permafrost' WHERE ano = 2022"
        f = _extract_simple_filters(sql, None)
        assert f.get("ano") == 2022


# ─────────────── query() — integration ───────────────────────────────────────

class TestQueryIntegration:
    @pytest.fixture
    def frozen_files(self, clientes_df, pedidos_df, tmp_dir):
        cp = os.path.join(tmp_dir, "clientes.permafrost")
        pp = os.path.join(tmp_dir, "pedidos.pf")
        pf.freeze(clientes_df, cp, partition_by="ano", primary_key="id")
        pf.freeze(pedidos_df,  pp, partition_by="ano", primary_key="pedido_id")
        return cp, pp

    def test_simple_select(self, frozen_files):
        cp, _ = frozen_files
        df = pf.query(f"SELECT * FROM '{cp}'")
        assert len(df) > 0
        assert "id" in df.columns

    def test_where_filter(self, frozen_files, clientes_df):
        cp, _ = frozen_files
        df = pf.query(f"SELECT * FROM '{cp}' WHERE ano = 2022")
        assert set(df["ano"].unique()) == {2022}
        expected = len(clientes_df[clientes_df["ano"] == 2022])
        assert len(df) == expected

    def test_join_two_files(self, frozen_files, clientes_df, pedidos_df):
        cp, pp = frozen_files
        sql = f"""
            SELECT c.id, c.nome, SUM(p.valor) AS total_pedidos
            FROM '{cp}' c
            JOIN '{pp}' p ON c.id = p.cliente_id
            GROUP BY c.id, c.nome
            ORDER BY c.id
        """
        df = pf.query(sql)
        assert "total_pedidos" in df.columns
        assert len(df) > 0

    def test_join_with_where(self, frozen_files):
        cp, pp = frozen_files
        sql = f"""
            SELECT c.id, c.regiao, p.valor
            FROM '{cp}' c
            JOIN '{pp}' p ON c.id = p.cliente_id
            WHERE c.regiao = 'Sul'
        """
        df = pf.query(sql)
        assert set(df["regiao"].unique()) == {"Sul"}

    def test_pf_extension_in_query(self, clientes_df, tmp_dir):
        path = os.path.join(tmp_dir, "c.pf")
        pf.freeze(clientes_df, path)
        df = pf.query(f"SELECT COUNT(*) AS n FROM '{path}'")
        assert df["n"].iloc[0] == len(clientes_df)

    def test_register_alias_query(self, frozen_files, clientes_df, tmp_dir):
        cp, _ = frozen_files
        alias = f"_test_cli_{os.getpid()}"
        register(alias, cp)
        try:
            df = pf.query(f"SELECT * FROM {alias} WHERE ano = 2021")
            expected = len(clientes_df[clientes_df["ano"] == 2021])
            assert len(df) == expected
        finally:
            unregister(alias)

    def test_register_single_arg(self, frozen_files, tmp_dir):
        cp, _ = frozen_files
        register(cp)
        alias = os.path.splitext(os.path.basename(cp))[0]
        try:
            assert alias in registered()
        finally:
            unregister(alias)

    def test_unregister_unknown_raises(self):
        with pytest.raises(KeyError):
            unregister("__nonexistent_alias__")

    def test_register_nonexistent_file_raises(self, tmp_dir):
        with pytest.raises(FileNotFoundError):
            register("ghost", os.path.join(tmp_dir, "nope.permafrost"))

    def test_register_wrong_extension_raises(self, tmp_dir):
        csv_path = os.path.join(tmp_dir, "data.csv")
        open(csv_path, "w").close()
        with pytest.raises(ValueError):
            register("data", csv_path)

    def test_query_returns_dataframe(self, frozen_files):
        cp, _ = frozen_files
        result = pf.query(f"SELECT id, nome FROM '{cp}' LIMIT 10")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 10

    def test_query_aggregation(self, frozen_files, clientes_df):
        cp, _ = frozen_files
        df = pf.query(f"SELECT ano, COUNT(*) AS n FROM '{cp}' GROUP BY ano ORDER BY ano")
        for row in df.itertuples():
            expected = len(clientes_df[clientes_df["ano"] == row.ano])
            assert row.n == expected

    def test_query_missing_file_raises(self, tmp_dir):
        ghost = os.path.join(tmp_dir, "ghost.permafrost")
        with pytest.raises(FileNotFoundError):
            pf.query(f"SELECT * FROM '{ghost}'")


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v", "--tb=short"])

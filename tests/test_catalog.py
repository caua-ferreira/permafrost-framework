"""
Testes do PermafrostCatalog.
Executar: pytest tests/test_catalog.py -v
"""
import os, tempfile, shutil
import pytest
import numpy as np
import pandas as pd
import permafrost as pf


@pytest.fixture(scope="module")
def catalog_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("pf_data")
    np.random.seed(42)

    def gen(n, start, codec, quant, part_by, name):
        df = pd.DataFrame({
            "id":     np.arange(1, n+1, dtype=np.int32),
            "data":   pd.date_range(start, periods=n, freq="1h"),
            "ano":    pd.date_range(start, periods=n, freq="1h").year.astype(np.int16),
            "regiao": np.random.choice(["Norte","Sul","Leste","Oeste"], n),
            "total":  np.round(np.random.uniform(1, 5000, n), 2),
            "status": np.random.choice(["Ativo","Inativo","Pendente"], n),
        })
        if part_by:
            df = df.sort_values(part_by).reset_index(drop=True)
        path = str(d / f"{name}.permafrost")
        pf.freeze(df, path, codec=codec, quant=quant, partition_by=part_by, chunk_rows=2000)
        return path

    gen(10000, "2022-01-01", pf.CODEC_LZMA2, pf.QUANT_NONE,   "ano", "vendas_2022")
    gen(12000, "2023-01-01", pf.CODEC_LZMA2, pf.QUANT_NONE,   "ano", "vendas_2023")
    gen(5000,  "2020-01-01", pf.CODEC_ZSTD,  pf.QUANT_NONE,   None,  "clientes")
    gen(8000,  "2021-01-01", pf.CODEC_LZMA2, pf.QUANT_MEDIUM, None,  "vendas_vault")
    return str(d)


@pytest.fixture
def cat(catalog_dir):
    c = pf.PermafrostCatalog(":memory:")
    c.register_dir(catalog_dir)
    return c


class TestRegister:
    def test_register_dir_conta(self, cat):
        assert cat.stats()["total_datasets"] == 4

    def test_idempotente(self, cat, catalog_dir):
        files = [f for f in os.listdir(catalog_dir) if f.endswith(".permafrost")]
        r = cat.register(os.path.join(catalog_dir, files[0]))
        assert r["status"] == "already_registered"

    def test_register_single(self, catalog_dir):
        c = pf.PermafrostCatalog(":memory:")
        files = [f for f in os.listdir(catalog_dir) if f.endswith(".permafrost")]
        r = c.register(os.path.join(catalog_dir, files[0]), tags=["test"])
        assert r["status"] == "registered"
        assert r["rows"] > 0

    def test_register_arquivo_inexistente(self):
        c = pf.PermafrostCatalog(":memory:")
        with pytest.raises(FileNotFoundError):
            c.register("/nao/existe.permafrost")

    def test_stats_totais(self, cat):
        s = cat.stats()
        assert s["total_rows"] == 35000   # 10k+12k+5k+8k
        assert s["total_chunks"] > 0
        assert s["lossless_count"] == 3
        assert s["vault_count"] == 1


class TestSearch:
    def test_search_all(self, cat):
        assert len(cat.search()) == 4

    def test_search_por_nome(self, cat):
        df = cat.search(name="vendas")
        assert len(df) == 3
        assert all("vendas" in n for n in df["name"])

    def test_search_por_codec(self, cat):
        df = cat.search(codec="zstd")
        assert len(df) == 1
        assert df.iloc[0]["name"] == "clientes"

    def test_search_lossless_only(self, cat):
        df = cat.search(lossless_only=True)
        assert len(df) == 3
        assert all(q == 0 for q in df["quant"])

    def test_search_partition_key(self, cat):
        df = cat.search(partition_key="2022")
        assert len(df) >= 1
        assert any("vendas_2022" in n for n in df["name"])

    def test_search_columns_contain(self, cat):
        assert len(cat.search(columns_contain="total")) == 4

    def test_search_min_rows(self, cat):
        assert len(cat.search(min_rows=10000)) == 2

    def test_search_sem_resultado(self, cat):
        assert len(cat.search(name="xyzabc_nao_existe")) == 0

    def test_search_chunks(self, cat):
        df = cat.search_chunks("vendas_2022")
        assert len(df) > 0
        assert "byte_offset" in df.columns
        assert "sha256" in df.columns


class TestCatalogThaw:
    def test_thaw_full(self, cat):
        df = cat.unfreeze("vendas_2022")
        assert len(df) == 10000

    def test_thaw_com_filter(self, cat):
        df = cat.unfreeze("vendas_2023", filter={"ano": 2023})
        assert len(df) > 0 and len(df) <= 12000

    def test_thaw_desconhecido_lanca_erro(self, cat):
        with pytest.raises(KeyError, match="nao_existe"):
            cat.unfreeze("nao_existe")


class TestIntegrityCheck:
    def test_all_ok(self, cat):
        ic = cat.integrity_check()
        assert (ic["status"] == "OK").all()
        assert (ic["chunks_fail"] == 0).all()

    def test_arquivo_ausente_reportado(self, catalog_dir, tmp_path):
        c = pf.PermafrostCatalog(":memory:")
        ghost = str(tmp_path / "ghost.permafrost")
        files = [f for f in os.listdir(catalog_dir) if f.endswith(".permafrost")]
        shutil.copy(os.path.join(catalog_dir, files[0]), ghost)
        c.register(ghost)
        os.remove(ghost)
        ic = c.integrity_check()
        assert (ic["status"] == "FILE_MISSING").any()


class TestCostReport:
    def test_retorna_todos_datasets(self, cat):
        assert len(cat.cost_report("glacier_deep")) == 4

    def test_custo_positivo(self, cat):
        assert (cat.cost_report("glacier_deep")["cost_monthly_usd"] >= 0).all()

    def test_annual_e_12x_monthly(self, cat):
        cr = cat.cost_report("glacier_deep")
        ratio = cr["cost_annual_usd"] / cr["cost_monthly_usd"]
        assert (abs(ratio - 12) < 0.001).all()

    def test_tiers_diferentes(self, cat):
        cheap    = cat.cost_report("glacier_deep")["cost_monthly_usd"].sum()
        expensive = cat.cost_report("s3_standard")["cost_monthly_usd"].sum()
        assert expensive > cheap


class TestSQL:
    def test_sql_count(self, cat):
        df = cat.sql("SELECT COUNT(*) as n FROM datasets")
        assert df.iloc[0]["n"] == 4

    def test_sql_join_chunks(self, cat):
        df = cat.sql("""
            SELECT d.name, COUNT(c.id) as n_chunks
            FROM datasets d JOIN chunks c ON c.dataset_id = d.id
            GROUP BY d.name ORDER BY n_chunks DESC
        """)
        assert len(df) == 4
        assert "n_chunks" in df.columns


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

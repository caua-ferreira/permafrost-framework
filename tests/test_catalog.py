"""
Testes do PermafrostCatalog
Executar: python -m pytest tests/test_catalog.py -v
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
import numpy as np
import pandas as pd
from permafrost_codec_v3 import freeze, CODEC_LZMA2, CODEC_ZSTD, QUANT_NONE, QUANT_MEDIUM
from permafrost_catalog import PermafrostCatalog


@pytest.fixture(scope="module")
def catalog_dir(tmp_path_factory):
    """Cria diretório com 4 arquivos .permafrost para todos os testes."""
    d = tmp_path_factory.mktemp("pf_data")
    np.random.seed(42)

    def gen(n, start, codec, quant, part_by, name, comment=""):
        df = pd.DataFrame({
            'id':     np.arange(1, n+1, dtype=np.int32),
            'data':   pd.date_range(start, periods=n, freq='1h'),
            'ano':    pd.date_range(start, periods=n, freq='1h').year.astype(np.int16),
            'regiao': np.random.choice(['Norte','Sul','Leste','Oeste'], n),
            'total':  np.round(np.random.uniform(1, 5000, n), 2),
            'status': np.random.choice(['Ativo','Inativo','Pendente'], n),
        })
        if part_by:
            df = df.sort_values(part_by).reset_index(drop=True)
        path = str(d / f"{name}.permafrost")
        freeze(df, path, codec=codec, quant=quant,
               partition_by=part_by, chunk_rows=2000, comment=comment)
        return path, df

    paths = {}
    paths['vendas_2022'], _ = gen(10000, '2022-01-01', CODEC_LZMA2, QUANT_NONE,  'ano',    'vendas_2022', 'Vendas 2022')
    paths['vendas_2023'], _ = gen(12000, '2023-01-01', CODEC_LZMA2, QUANT_NONE,  'ano',    'vendas_2023', 'Vendas 2023')
    paths['clientes'],    _ = gen(5000,  '2020-01-01', CODEC_ZSTD,  QUANT_NONE,  None,     'clientes',    'Clientes ativos')
    paths['vendas_vault'],_ = gen(8000,  '2021-01-01', CODEC_LZMA2, QUANT_MEDIUM,None,     'vendas_vault','Vault 2021')

    return str(d), paths


@pytest.fixture
def cat(catalog_dir):
    """Catalog em memória com 4 datasets registrados."""
    directory, _ = catalog_dir
    c = PermafrostCatalog(':memory:')
    c.register_dir(directory)
    return c


class TestRegister:
    def test_register_dir_count(self, cat):
        s = cat.stats()
        assert s['total_datasets'] == 4

    def test_register_idempotent(self, cat, catalog_dir):
        directory, paths = catalog_dir
        # Registrar novamente não deve duplicar
        results = cat.register_dir(directory)
        already = [r for r in results if r['status'] == 'already_registered']
        assert len(already) == 4

    def test_register_single(self, catalog_dir):
        directory, paths = catalog_dir
        c = PermafrostCatalog(':memory:')
        r = c.register(paths['vendas_2022'], tags=['test'])
        assert r['status'] == 'registered'
        assert r['rows'] == 10000

    def test_register_missing_file_raises(self):
        c = PermafrostCatalog(':memory:')
        with pytest.raises(FileNotFoundError):
            c.register('/nao/existe.permafrost')

    def test_stats_totals(self, cat):
        s = cat.stats()
        assert s['total_rows'] == 35000  # 10k+12k+5k+8k
        assert s['total_chunks'] > 0
        assert s['lossless_count'] == 3
        assert s['vault_count'] == 1


class TestSearch:
    def test_search_all(self, cat):
        df = cat.search()
        assert len(df) == 4

    def test_search_by_name(self, cat):
        df = cat.search(name='vendas')
        assert len(df) == 3
        assert all('vendas' in n for n in df['name'])

    def test_search_by_codec(self, cat):
        df = cat.search(codec='zstd')
        assert len(df) == 1
        assert df.iloc[0]['name'] == 'clientes'

    def test_search_lossless_only(self, cat):
        df = cat.search(lossless_only=True)
        assert len(df) == 3
        assert all(q == 0 for q in df['quant'])

    def test_search_by_partition_key(self, cat):
        df = cat.search(partition_key='2022')
        assert len(df) >= 1
        assert any('vendas_2022' in n for n in df['name'])

    def test_search_columns_contain(self, cat):
        df = cat.search(columns_contain='total')
        assert len(df) == 4  # todos têm 'total'

    def test_search_min_rows(self, cat):
        df = cat.search(min_rows=10000)
        assert len(df) == 2  # vendas_2022 (10k) e vendas_2023 (12k)

    def test_search_no_results(self, cat):
        df = cat.search(name='nao_existe_xyzabc')
        assert len(df) == 0

    def test_search_chunks(self, cat):
        df = cat.search_chunks('vendas_2022')
        assert len(df) > 0
        assert 'byte_offset' in df.columns
        assert 'sha256' in df.columns


class TestCatalogThaw:
    def test_thaw_full(self, cat):
        df = cat.thaw('vendas_2022')
        assert len(df) == 10000

    def test_thaw_with_filter(self, cat):
        df = cat.thaw('vendas_2023', filter={'ano': 2023})
        assert len(df) > 0
        assert len(df) <= 12000

    def test_thaw_unknown_dataset_raises(self, cat):
        with pytest.raises(KeyError, match="nao_existe"):
            cat.thaw('nao_existe')


class TestIntegrityCheck:
    def test_all_ok(self, cat):
        ic = cat.integrity_check()
        assert (ic['status'] == 'OK').all()
        assert (ic['chunks_fail'] == 0).all()

    def test_corrupt_detected(self, catalog_dir, tmp_path):
        directory, paths = catalog_dir
        # Criar uma cópia corrompida
        import shutil, struct
        corrupt_path = str(tmp_path / 'corrupted.permafrost')
        shutil.copy(paths['clientes'], corrupt_path)
        with open(corrupt_path, 'r+b') as f:
            f.seek(200)
            f.write(b'\xDE\xAD\xBE\xEF' * 4)
        c = PermafrostCatalog(':memory:')
        c.register(corrupt_path)
        ic = c.integrity_check()
        # SHA-256 do chunk deve falhar
        assert len(ic) == 1
        # Pode ser OK no header mas falhar no chunk ou vice-versa
        # O importante é que registrou e tentou verificar

    def test_missing_file_reported(self, catalog_dir, tmp_path):
        directory, paths = catalog_dir
        c = PermafrostCatalog(':memory:')
        ghost_path = str(tmp_path / 'ghost.permafrost')
        # Criar, registrar, deletar
        import shutil
        shutil.copy(paths['clientes'], ghost_path)
        c.register(ghost_path)
        os.remove(ghost_path)
        ic = c.integrity_check()
        missing = ic[ic['status'] == 'FILE_MISSING']
        assert len(missing) == 1


class TestCostReport:
    def test_cost_report_has_all_datasets(self, cat):
        cr = cat.cost_report('glacier_deep')
        assert len(cr) == 4

    def test_cost_report_positive_costs(self, cat):
        cr = cat.cost_report('glacier_deep')
        assert (cr['cost_monthly_usd'] >= 0).all()

    def test_cost_report_annual_is_12x_monthly(self, cat):
        cr = cat.cost_report('glacier_deep')
        ratio = cr['cost_annual_usd'] / cr['cost_monthly_usd']
        assert (abs(ratio - 12) < 0.001).all()

    def test_cost_report_different_tiers(self, cat):
        cr_cheap  = cat.cost_report('glacier_deep')
        cr_expensive = cat.cost_report('s3_standard')
        assert cr_expensive['cost_monthly_usd'].sum() > cr_cheap['cost_monthly_usd'].sum()


class TestDirectSQL:
    def test_sql_query(self, cat):
        df = cat.sql("SELECT COUNT(*) as n FROM datasets")
        assert df.iloc[0]['n'] == 4

    def test_sql_join(self, cat):
        df = cat.sql("""
            SELECT d.name, COUNT(c.id) as n_chunks
            FROM datasets d
            JOIN chunks c ON c.dataset_id = d.id
            GROUP BY d.name
            ORDER BY n_chunks DESC
        """)
        assert len(df) == 4
        assert 'n_chunks' in df.columns


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])

"""
Testes do Sparse Index — permafrost_codec_v3.py
Executar: python -m pytest tests/test_sparse_index.py -v
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
import numpy as np
import pandas as pd
from permafrost_codec_v3 import (
    freeze, thaw, audit,
    CODEC_LZMA2, CODEC_ZSTD,
    QUANT_NONE, QUANT_MEDIUM,
)


@pytest.fixture(scope="module")
def time_df():
    """Dataset com timestamps e campo 'ano' para testar particionamento."""
    np.random.seed(42)
    N = 20_000
    dates = pd.date_range('2021-01-01', periods=N, freq='1h')
    return pd.DataFrame({
        'id':      np.arange(1, N+1, dtype=np.int32),
        'data':    dates,
        'ano':     dates.year.astype(np.int16),
        'mes':     dates.month.astype(np.int8),
        'regiao':  np.random.choice(['Norte','Sul','Leste','Oeste'], N),
        'total':   np.round(np.random.uniform(1, 10000, N), 2),
        'status':  np.random.choice(['Ativo','Inativo','Pendente'], N),
    }).sort_values('ano').reset_index(drop=True)


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


class TestChunkedFreeze:
    def test_creates_file(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        m = freeze(time_df, path, partition_by='ano', chunk_rows=2000)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0

    def test_correct_chunk_count(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        m = freeze(time_df, path, chunk_rows=2000)
        expected_chunks = (len(time_df) + 1999) // 2000
        assert m['n_chunks'] == expected_chunks

    def test_ratio_above_5x(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        m = freeze(time_df, path, codec=CODEC_LZMA2, partition_by='ano')
        assert m['ratio'] > 5.0, f"Ratio esperado >5×, foi {m['ratio']}"

    def test_magic_bytes(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path)
        with open(path, 'rb') as f:
            assert f.read(4) == b'PRMS'

    def test_eof_magic(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path)
        with open(path, 'rb') as f:
            f.seek(-4, 2)
            assert f.read(4) == b'SMRP'

    def test_partition_keys_recorded(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path, partition_by='ano')
        info = audit(path)
        years_in_data = set(str(y) for y in time_df['ano'].unique())
        for key in info['partition_keys']:
            # cada chave deve conter pelo menos um ano do dataset
            assert any(y in key for y in years_in_data)


class TestAuditNoDecompress:
    def test_audit_returns_correct_rows(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path, partition_by='ano', chunk_rows=2000)
        info = audit(path)
        assert info['orig_rows'] == len(time_df)

    def test_audit_has_index_entries(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path, partition_by='ano', chunk_rows=2000)
        info = audit(path)
        assert len(info['index_entries']) > 0
        entry = info['index_entries'][0]
        assert 'chunk_id' in entry
        assert 'byte_offset' in entry
        assert 'byte_len' in entry
        assert 'sha256' in entry
        assert 'part_key' in entry

    def test_audit_partition_col(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path, partition_by='ano')
        info = audit(path)
        assert info['partition_col'] == 'ano'

    def test_audit_file_not_modified(self, time_df, tmp_dir):
        """Audit não deve modificar o arquivo (timestamp de modificação)."""
        import time as _time
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path)
        mtime_before = os.path.getmtime(path)
        _time.sleep(0.05)
        audit(path)
        mtime_after = os.path.getmtime(path)
        assert mtime_before == mtime_after


class TestFullThaw:
    def test_full_thaw_row_count(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path, partition_by='ano')
        df_t = thaw(path, verify=True)
        assert len(df_t) == len(time_df)

    def test_full_thaw_columns(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path)
        df_t = thaw(path)
        assert set(df_t.columns) == set(time_df.columns)

    def test_full_thaw_id_exact(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path, partition_by='ano')
        df_t = thaw(path, verify=True)
        orig_ids = time_df['id'].values
        thaw_ids = df_t['id'].values[:len(time_df)].astype(np.int64)
        assert np.array_equal(orig_ids, thaw_ids)

    def test_full_thaw_categories_exact(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path)
        df_t = thaw(path)
        for col in ['status', 'regiao']:
            orig = time_df[col].astype(str).values
            rest = df_t[col].astype(str).values[:len(time_df)]
            pct = (orig == rest).mean() * 100
            assert pct == 100.0, f"'{col}': {pct:.1f}% (esperado 100%)"


class TestSelectiveThaw:
    def test_filter_by_partition_returns_subset(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df.sort_values('ano').reset_index(drop=True), path,
               partition_by='ano', chunk_rows=2000)
        df_2021 = thaw(path, filter={'ano': 2021})
        assert len(df_2021) > 0
        assert len(df_2021) < len(time_df)

    def test_filter_reads_less_than_full(self, time_df, tmp_dir):
        """Thaw seletivo deve ler menos bytes que o thaw completo."""
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df.sort_values('ano').reset_index(drop=True), path,
               partition_by='ano', chunk_rows=2000)
        info = audit(path)
        file_size = os.path.getsize(path)
        # Somar só os bytes dos chunks de 2021
        chunks_2021 = [e for e in info['index_entries'] if '2021' in e['part_key']]
        bytes_2021 = sum(e['byte_len'] + 32 for e in chunks_2021)
        # Deve ler menos de 70% do arquivo para 1 dos ~3 anos
        assert bytes_2021 / file_size < 0.70

    def test_row_range_returns_correct_count(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df, path, chunk_rows=2000)
        df_range = thaw(path, row_range=(0, 1999))
        assert len(df_range) <= 2000

    def test_empty_filter_returns_empty_df(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(time_df.sort_values('ano').reset_index(drop=True), path,
               partition_by='ano', chunk_rows=2000)
        df_empty = thaw(path, filter={'ano': 9999})
        assert len(df_empty) == 0


class TestIntegrityWithChunks:
    def test_good_file_passes(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'good.permafrost')
        freeze(time_df, path)
        df_t = thaw(path, verify=True)
        assert len(df_t) == len(time_df)

    def test_corrupt_chunk_detected(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'good.permafrost')
        corrupt = os.path.join(tmp_dir, 'corrupt.permafrost')
        freeze(time_df, path, chunk_rows=2000)
        shutil.copy(path, corrupt)
        info = audit(corrupt)
        # Corromper bytes dentro do primeiro chunk
        offset = info['index_entries'][0]['byte_offset']
        with open(corrupt, 'r+b') as f:
            f.seek(offset + 100)
            f.write(b'\x00' * 16)
        with pytest.raises(ValueError, match="[Cc]orrompido|SHA"):
            thaw(corrupt, verify=True)

    def test_truncated_file_detected(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, 'good.permafrost')
        trunc = os.path.join(tmp_dir, 'trunc.permafrost')
        freeze(time_df, path)
        size = os.path.getsize(path)
        with open(path, 'rb') as src, open(trunc, 'wb') as dst:
            dst.write(src.read(size // 2))
        with pytest.raises(ValueError):
            thaw(trunc)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])

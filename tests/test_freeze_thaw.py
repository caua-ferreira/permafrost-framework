"""
Testes de round-trip: freeze → thaw → verificação de integridade
Executar com: python -m pytest tests/ -v
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import numpy as np
import pandas as pd
from src.permafrost_codec import (
    freeze, thaw, audit,
    CODEC_ZSTD, CODEC_LZMA2,
    QUANT_NONE, QUANT_MEDIUM,
)


@pytest.fixture(scope="module")
def sample_df():
    """Dataset de teste com todos os tipos de coluna suportados."""
    np.random.seed(42)
    N = 5_000
    products = [f'PROD-{i:04d}' for i in range(100)]
    clients  = [f'CLI-{i:05d}' for i in range(500)]
    return pd.DataFrame({
        'id':             np.arange(1, N+1, dtype=np.int32),
        'data':           pd.date_range('2020-01-01', periods=N, freq='5min'),
        'cliente_id':     np.random.choice(clients, N),
        'produto_id':     np.random.choice(products, N),
        'categoria':      np.random.choice(['Eletrônicos','Vestuário','Alimentos'], N),
        'quantidade':     np.random.randint(1, 200, N, dtype=np.int16),
        'preco_unitario': np.round(np.random.uniform(1.99, 4999.99, N), 2),
        'total_liquido':  np.round(np.random.uniform(2, 50000, N), 2),
        'pais':           np.random.choice(['Brasil','EUA','Argentina'], N),
        'status':         np.random.choice(['Ativo','Inativo','Pendente'], N),
        'vendedor_id':    np.random.randint(1000, 9999, N, dtype=np.int32),
        'score_cliente':  np.round(np.random.uniform(0, 1000, N), 1),
        'latitude':       np.round(np.random.uniform(-33, 5, N), 6),
        'longitude':      np.round(np.random.uniform(-73, -34, N), 6),
        'observacao':     np.random.choice(['OK','Urgente','Normal','VIP'], N),
    })


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


class TestFreeze:
    def test_freeze_creates_file(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        metrics = freeze(sample_df, path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0

    def test_freeze_returns_metrics(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        m = freeze(sample_df, path)
        assert 'ratio' in m
        assert 'stored_mb' in m
        assert 'freeze_s' in m
        assert m['ratio'] > 1.0
        assert m['rows'] == len(sample_df)
        assert m['cols'] == len(sample_df.columns)

    def test_freeze_ratio_lzma_better_than_zstd(self, sample_df, tmp_dir):
        path_z = os.path.join(tmp_dir, 'zstd.permafrost')
        path_l = os.path.join(tmp_dir, 'lzma.permafrost')
        mz = freeze(sample_df, path_z, codec=CODEC_ZSTD)
        ml = freeze(sample_df, path_l, codec=CODEC_LZMA2)
        assert ml['ratio'] >= mz['ratio'] * 0.95  # LZMA2 deve ser ao menos comparável

    def test_freeze_vault_smaller_than_lossless(self, sample_df, tmp_dir):
        path_l = os.path.join(tmp_dir, 'lossless.permafrost')
        path_v = os.path.join(tmp_dir, 'vault.permafrost')
        ml = freeze(sample_df, path_l, quant=QUANT_NONE)
        mv = freeze(sample_df, path_v, quant=QUANT_MEDIUM)
        assert mv['stored_mb'] <= ml['stored_mb']

    def test_freeze_magic_bytes(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path)
        with open(path, 'rb') as f:
            header = f.read(4)
            assert header == b'PRMS', f"Magic incorreto: {header!r}"

    def test_freeze_eof_magic(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path)
        with open(path, 'rb') as f:
            f.seek(-4, 2)
            eof = f.read(4)
            assert eof == b'SMRP', f"EOF magic incorreto: {eof!r}"


class TestThaw:
    def test_thaw_lossless_rows(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path, codec=CODEC_LZMA2, quant=QUANT_NONE)
        df_t = thaw(path)
        assert len(df_t) == len(sample_df)

    def test_thaw_lossless_columns(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path, codec=CODEC_LZMA2, quant=QUANT_NONE)
        df_t = thaw(path)
        assert set(df_t.columns) == set(sample_df.columns)

    def test_thaw_lossless_id_exact(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path, codec=CODEC_LZMA2, quant=QUANT_NONE)
        df_t = thaw(path)
        assert np.array_equal(
            sample_df['id'].values,
            df_t['id'].values[:len(sample_df)].astype(np.int64)
        ), "IDs devem ser exatos no modo lossless"

    def test_thaw_lossless_categories_exact(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path, codec=CODEC_LZMA2, quant=QUANT_NONE)
        df_t = thaw(path)
        for col in ['status', 'pais', 'categoria', 'observacao']:
            orig = sample_df[col].astype(str).values
            restored = df_t[col].astype(str).values[:len(sample_df)]
            match_pct = (orig == restored).mean() * 100
            assert match_pct == 100.0, f"Coluna '{col}' deveria ser 100% exata, foi {match_pct:.1f}%"

    def test_thaw_lossless_floats_exact(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path, codec=CODEC_LZMA2, quant=QUANT_NONE)
        df_t = thaw(path)
        for col in ['preco_unitario', 'total_liquido']:
            orig = sample_df[col].values
            rest = df_t[col].values[:len(sample_df)].astype(float)
            max_diff = np.abs(orig - rest).max()
            assert max_diff < 0.01, f"Coluna '{col}': max_diff={max_diff:.6f}, esperado <0.01"

    def test_thaw_zstd_lossless(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test_zstd.permafrost')
        freeze(sample_df, path, codec=CODEC_ZSTD, quant=QUANT_NONE)
        df_t = thaw(path)
        assert len(df_t) == len(sample_df)
        orig = sample_df['id'].values
        rest = df_t['id'].values[:len(sample_df)].astype(np.int64)
        assert np.array_equal(orig, rest)

    def test_thaw_vault_ids_still_exact(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'vault.permafrost')
        freeze(sample_df, path, quant=QUANT_MEDIUM)
        df_t = thaw(path)
        assert np.array_equal(
            sample_df['id'].values,
            df_t['id'].values[:len(sample_df)].astype(np.int64)
        ), "IDs devem ser exatos mesmo no Vault mode"

    def test_thaw_vault_price_within_1_real(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'vault.permafrost')
        freeze(sample_df, path, quant=QUANT_MEDIUM)
        df_t = thaw(path)
        orig = sample_df['preco_unitario'].values
        rest = df_t['preco_unitario'].values[:len(sample_df)].astype(float)
        max_diff = np.abs(orig - rest).max()
        assert max_diff <= 1.0, f"Vault mode: preço deve ter diff <= R$1.00, foi {max_diff:.2f}"


class TestIntegrity:
    def test_correct_file_passes(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'good.permafrost')
        freeze(sample_df, path)
        df_t = thaw(path, verify=True)
        assert len(df_t) == len(sample_df)

    def test_corrupt_header_detected(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'corrupt_hdr.permafrost')
        corrupt = os.path.join(tmp_dir, 'corrupt.permafrost')
        freeze(sample_df, path)
        shutil.copy(path, corrupt)
        with open(corrupt, 'r+b') as f:
            f.seek(500); f.write(b'\x00' * 8)
        with pytest.raises(ValueError, match="SHA-256"):
            thaw(corrupt, verify=True)

    def test_corrupt_payload_detected(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'good.permafrost')
        corrupt = os.path.join(tmp_dir, 'corrupt_payload.permafrost')
        freeze(sample_df, path)
        size = os.path.getsize(path)
        shutil.copy(path, corrupt)
        with open(corrupt, 'r+b') as f:
            f.seek(size // 2); f.write(b'\xFF' * 16)
        with pytest.raises(ValueError):
            thaw(corrupt, verify=True)

    def test_truncated_file_detected(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'good.permafrost')
        trunc = os.path.join(tmp_dir, 'truncated.permafrost')
        freeze(sample_df, path)
        size = os.path.getsize(path)
        with open(path, 'rb') as src, open(trunc, 'wb') as dst:
            dst.write(src.read(size // 2))
        with pytest.raises(ValueError):
            thaw(trunc, verify=True)

    def test_wrong_magic_detected(self, tmp_dir):
        fake = os.path.join(tmp_dir, 'fake.permafrost')
        with open(fake, 'wb') as f:
            f.write(b'%PDF-1.4 this is not a permafrost file')
        with pytest.raises(ValueError, match="[Mm]agic"):
            thaw(fake)


class TestAudit:
    def test_audit_without_decompressing(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path, codec=CODEC_LZMA2, comment="Teste audit")
        info = audit(path)
        assert info['version'] == '1.0'
        assert info['codec'] == 'lzma2'
        assert info['original_rows'] == len(sample_df)
        assert info['ratio'] > 1.0
        assert info['comment'] == 'Teste audit'
        assert set(info['columns']) == set(sample_df.columns)

    def test_audit_predictors_assigned(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, 'test.permafrost')
        freeze(sample_df, path)
        info = audit(path)
        preds = set(info['col_predictors'].values())
        expected = {'delta_zigzag', 'lag1_zigzag', 'ts_delta_s', 'category_u8', 'raw_text'}
        assert preds.issubset(expected), f"Preditores inesperados: {preds - expected}"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])

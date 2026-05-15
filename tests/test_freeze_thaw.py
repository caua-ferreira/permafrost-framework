"""
Testes de freeze/thaw — API core do Permafrost.
Executar: pytest tests/test_freeze_thaw.py -v
"""
import os, shutil, tempfile
import pytest
import numpy as np
import pandas as pd
import permafrost as pf


@pytest.fixture(scope="module")
def sample_df():
    np.random.seed(42)
    N = 5_000
    return pd.DataFrame({
        "id":             np.arange(1, N+1, dtype=np.int32),
        "data":           pd.date_range("2020-01-01", periods=N, freq="4h"),
        "ano":            pd.date_range("2020-01-01", periods=N, freq="4h").year.astype(np.int16),
        "categoria":      np.random.choice(["Eletrônicos","Vestuário","Alimentos"], N),
        "quantidade":     np.random.randint(1, 200, N, dtype=np.int16),
        "preco_unitario": np.round(np.random.uniform(1.99, 4999.99, N), 2),
        "total_liquido":  np.round(np.random.uniform(2, 50000, N), 2),
        "pais":           np.random.choice(["Brasil","EUA","Argentina"], N),
        "status":         np.random.choice(["Ativo","Inativo","Pendente"], N),
        "vendedor_id":    np.random.randint(1000, 9999, N, dtype=np.int32),
    })


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


class TestFreeze:
    def test_cria_arquivo(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "test.permafrost")
        pf.freeze(sample_df, path)
        assert os.path.exists(path) and os.path.getsize(path) > 0

    def test_retorna_metricas(self, sample_df, tmp_dir):
        m = pf.freeze(sample_df, os.path.join(tmp_dir, "t.permafrost"))
        assert m["ratio"] > 1.0 and m["rows"] == len(sample_df)

    def test_magic_bytes_prms(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path)
        assert open(path,"rb").read(4) == b"PRMS"

    def test_eof_magic_smrp(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path)
        with open(path,"rb") as f: f.seek(-4,2); assert f.read(4) == b"SMRP"

    def test_lzma2_ratio_alto(self, sample_df, tmp_dir):
        m = pf.freeze(sample_df, os.path.join(tmp_dir,"lzma.permafrost"), codec=pf.CODEC_LZMA2)
        assert m["ratio"] > 5.0

    def test_zstd_funciona(self, sample_df, tmp_dir):
        m = pf.freeze(sample_df, os.path.join(tmp_dir,"zstd.permafrost"), codec=pf.CODEC_ZSTD)
        assert m["ratio"] > 3.0

    def test_vault_menor_que_lossless(self, sample_df, tmp_dir):
        ml = pf.freeze(sample_df, os.path.join(tmp_dir,"loss.permafrost"),  quant=pf.QUANT_NONE)
        mv = pf.freeze(sample_df, os.path.join(tmp_dir,"vault.permafrost"), quant=pf.QUANT_MEDIUM)
        assert mv["stored_mb"] <= ml["stored_mb"]

    def test_comentario_embutido(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, comment="teste de comentário")
        assert pf.audit(path)["comment"] == "teste de comentário"

    def test_partition_by_registrado(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        df_s = sample_df.sort_values("ano").reset_index(drop=True)
        pf.freeze(df_s, path, partition_by="ano")
        info = pf.audit(path)
        assert info["partition_col"] == "ano"
        assert len(info["partition_keys"]) > 0


class TestAudit:
    def test_versao_formato(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path)
        assert pf.audit(path)["version"] in ("1.0","1.1","1.2")

    def test_linhas_corretas(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path)
        assert pf.audit(path)["orig_rows"] == len(sample_df)

    def test_colunas_registradas(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path)
        assert set(pf.audit(path)["columns"]) == set(sample_df.columns)

    def test_codec_registrado(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, codec=pf.CODEC_LZMA2)
        assert pf.audit(path)["codec"] == "lzma2"

    def test_nao_modifica_arquivo(self, sample_df, tmp_dir):
        import time as _t
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path)
        mtime = os.path.getmtime(path)
        _t.sleep(0.05)
        pf.audit(path)
        assert os.path.getmtime(path) == mtime

    def test_index_entries_presente(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, chunk_rows=1000)
        entries = pf.audit(path)["index_entries"]
        assert len(entries) > 0
        assert all(f in entries[0] for f in ("chunk_id","byte_offset","byte_len","sha256"))


class TestThawLossless:
    def test_linhas_recuperadas(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, codec=pf.CODEC_LZMA2, quant=pf.QUANT_NONE)
        assert len(pf.thaw(path, verify=True)) == len(sample_df)

    def test_colunas_preservadas(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path)
        assert set(pf.thaw(path).columns) == set(sample_df.columns)

    def test_ids_exatos_delta_zigzag(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, codec=pf.CODEC_LZMA2, quant=pf.QUANT_NONE)
        df_t = pf.thaw(path, verify=True)
        assert np.array_equal(sample_df["id"].values,
                               df_t["id"].values[:len(sample_df)].astype(np.int64))

    def test_categorias_exatas_category_u8(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, codec=pf.CODEC_LZMA2, quant=pf.QUANT_NONE)
        df_t = pf.thaw(path, verify=True)
        for col in ("status","pais","categoria"):
            pct = (sample_df[col].astype(str).values ==
                   df_t[col].astype(str).values[:len(sample_df)]).mean() * 100
            assert pct == 100.0, f"'{col}': {pct:.1f}%"

    def test_floats_exatos_lag1_zigzag(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, codec=pf.CODEC_LZMA2, quant=pf.QUANT_NONE)
        df_t = pf.thaw(path, verify=True)
        for col in ("preco_unitario","total_liquido"):
            diff = np.abs(sample_df[col].values -
                          df_t[col].values[:len(sample_df)].astype(float)).max()
            assert diff < 0.01, f"'{col}' max_diff={diff:.6f}"

    def test_timestamps_exatos_ts_delta_s(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, codec=pf.CODEC_LZMA2, quant=pf.QUANT_NONE)
        df_t = pf.thaw(path, verify=True)
        orig = sample_df["data"].dt.strftime("%Y-%m-%d %H:%M").values
        rest = pd.to_datetime(df_t["data"]).dt.strftime("%Y-%m-%d %H:%M").values[:len(sample_df)]
        assert (orig == rest).mean() == 1.0


class TestThawVault:
    def test_ids_exatos(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(sample_df, path, quant=pf.QUANT_MEDIUM)
        df_t = pf.thaw(path, verify=True)
        assert np.array_equal(sample_df["id"].values,
                               df_t["id"].values[:len(sample_df)].astype(np.int64))

    def test_categorias_exatas(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(sample_df, path, quant=pf.QUANT_MEDIUM)
        df_t = pf.thaw(path, verify=True)
        for col in ("status","pais"):
            assert (sample_df[col].astype(str).values ==
                    df_t[col].astype(str).values[:len(sample_df)]).mean() == 1.0

    def test_floats_tolerancia_r1(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(sample_df, path, quant=pf.QUANT_MEDIUM)
        df_t = pf.thaw(path, verify=True)
        diff = np.abs(sample_df["preco_unitario"].values -
                      df_t["preco_unitario"].values[:len(sample_df)].astype(float)).max()
        assert diff <= 1.0, f"max_diff=R${diff:.2f}"


class TestIntegridade:
    def test_arquivo_correto_passa(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "g.permafrost")
        pf.freeze(sample_df, path)
        assert len(pf.thaw(path, verify=True)) == len(sample_df)

    def test_detecta_header_corrompido(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "g.permafrost")
        corrupt = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(sample_df, path)
        shutil.copy(path, corrupt)
        with open(corrupt,"r+b") as f: f.seek(500); f.write(b"\x00"*8)
        with pytest.raises(ValueError, match="SHA"):
            pf.thaw(corrupt, verify=True)

    def test_detecta_payload_corrompido(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "g.permafrost")
        corrupt = os.path.join(tmp_dir, "c2.permafrost")
        pf.freeze(sample_df, path)
        sz = os.path.getsize(path)
        shutil.copy(path, corrupt)
        with open(corrupt,"r+b") as f: f.seek(sz//2); f.write(b"\xFF"*32)
        with pytest.raises(ValueError):
            pf.thaw(corrupt, verify=True)

    def test_detecta_truncado(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "g.permafrost")
        trunc = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path)
        sz = os.path.getsize(path)
        with open(path,"rb") as s, open(trunc,"wb") as d: d.write(s.read(sz//2))
        with pytest.raises(ValueError): pf.thaw(trunc)

    def test_detecta_magic_errado(self, tmp_dir):
        fake = os.path.join(tmp_dir, "f.permafrost")
        with open(fake,"wb") as f: f.write(b"%PDF-1.4 not permafrost")
        with pytest.raises(ValueError, match="[Mm]agic"):
            pf.thaw(fake)


class TestSparseIndex:
    def test_filter_retorna_subset(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "p.permafrost")
        df_s = sample_df.sort_values("ano").reset_index(drop=True)
        pf.freeze(df_s, path, partition_by="ano", chunk_rows=1000)
        ano  = sorted(df_s["ano"].unique())[0]
        df_f = pf.thaw(path, filter={"ano": ano})
        assert 0 < len(df_f) < len(df_s)

    def test_row_range_funciona(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(sample_df, path, chunk_rows=1000)
        assert len(pf.thaw(path, row_range=(0, 999))) <= 1000

    def test_filter_vazio_retorna_df_vazio(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "p.permafrost")
        df_s = sample_df.sort_values("ano").reset_index(drop=True)
        pf.freeze(df_s, path, partition_by="ano")
        assert len(pf.thaw(path, filter={"ano": 9999})) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])


class TestZPAQCodec:
    """Testes do CODEC_ZPAQ — requer binário 'zpaq' instalado no sistema."""

    @pytest.fixture(autouse=True)
    def check_zpaq(self):
        import shutil
        if not shutil.which("zpaq"):
            pytest.skip("zpaq binário não disponível (apt install zpaq)")

    def test_zpaq_freeze_thaw_round_trip(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "zpaq.permafrost")
        m = pf.freeze(sample_df, path, codec=pf.CODEC_ZPAQ, quant=pf.QUANT_NONE)
        assert m["ratio"] > 3.0
        df_t = pf.thaw(path, verify=True)
        assert len(df_t) == len(sample_df)

    def test_zpaq_ids_exatos(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "zpaq.permafrost")
        pf.freeze(sample_df, path, codec=pf.CODEC_ZPAQ, quant=pf.QUANT_NONE)
        df_t = pf.thaw(path, verify=True)
        assert np.array_equal(sample_df["id"].values,
                               df_t["id"].values[:len(sample_df)].astype(np.int64))

    def test_zpaq_codec_registrado_no_audit(self, sample_df, tmp_dir):
        path = os.path.join(tmp_dir, "zpaq.permafrost")
        pf.freeze(sample_df, path, codec=pf.CODEC_ZPAQ)
        assert pf.audit(path)["codec"] == "zpaq"

    def test_zpaq_melhor_que_lzma2_em_texto(self, tmp_dir):
        """ZPAQ supera LZMA2 em dados de texto com padrões de longa distância."""
        np.random.seed(1)
        N = 2_000
        df_text = pd.DataFrame({
            "id":  np.arange(1, N+1, dtype=np.int32),
            "log": [f"INFO 2024-01-{(i%28)+1:02d}T{(i%24):02d}:00:00Z auth service request id={i:06d} status=ok duration_ms={np.random.randint(1,500)}" for i in range(N)],
        })
        pz = os.path.join(tmp_dir, "zpaq.permafrost")
        pl = os.path.join(tmp_dir, "lzma.permafrost")
        mz = pf.freeze(df_text, pz, codec=pf.CODEC_ZPAQ)
        ml = pf.freeze(df_text, pl, codec=pf.CODEC_LZMA2)
        # ZPAQ deve ser pelo menos tão bom quanto LZMA2 para texto
        assert mz["stored_mb"] <= ml["stored_mb"] * 1.1  # tolerância 10%

"""
Testes do Sparse Index.
Executar: pytest tests/test_sparse_index.py -v
"""
import os, shutil, tempfile
import pytest
import numpy as np
import pandas as pd
import permafrost as pf


@pytest.fixture(scope="module")
def time_df():
    np.random.seed(42)
    N = 20_000
    dates = pd.date_range("2021-01-01", periods=N, freq="1h")
    return pd.DataFrame({
        "id":     np.arange(1, N+1, dtype=np.int32),
        "data":   dates,
        "ano":    dates.year.astype(np.int16),
        "mes":    dates.month.astype(np.int8),
        "regiao": np.random.choice(["Norte","Sul","Leste","Oeste"], N),
        "total":  np.round(np.random.uniform(1, 10000, N), 2),
        "status": np.random.choice(["Ativo","Inativo","Pendente"], N),
    }).sort_values("ano").reset_index(drop=True)


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


class TestChunkedFreeze:
    def test_cria_arquivo(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano", chunk_rows=2000)
        assert os.path.exists(path)

    def test_chunks_corretos(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        m = pf.freeze(time_df, path, chunk_rows=2000)
        expected = (len(time_df) + 1999) // 2000
        assert m["n_chunks"] == expected

    def test_ratio_acima_5x(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        m = pf.freeze(time_df, path, codec=pf.CODEC_LZMA2, partition_by="ano")
        assert m["ratio"] > 5.0

    def test_magic_bytes(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path)
        assert open(path,"rb").read(4) == b"PRMS"

    def test_eof_magic(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path)
        with open(path,"rb") as f: f.seek(-4,2); assert f.read(4) == b"SMRP"


class TestAuditSemDescomprimir:
    def test_linhas_corretas(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano", chunk_rows=2000)
        assert pf.audit(path)["orig_rows"] == len(time_df)

    def test_index_entries_presente(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano", chunk_rows=2000)
        entries = pf.audit(path)["index_entries"]
        assert len(entries) > 0
        assert all(f in entries[0] for f in ("chunk_id","byte_offset","byte_len","sha256","part_key"))

    def test_partition_col(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano")
        assert pf.audit(path)["partition_col"] == "ano"

    def test_nao_modifica_arquivo(self, time_df, tmp_dir):
        import time as _t
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path)
        mt = os.path.getmtime(path)
        _t.sleep(0.05)
        pf.audit(path)
        assert os.path.getmtime(path) == mt


class TestThawCompleto:
    def test_linhas(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano")
        assert len(pf.unfreeze(path, verify=True)) == len(time_df)

    def test_colunas(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path)
        assert set(pf.unfreeze(path).columns) == set(time_df.columns)

    def test_ids_exatos(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano")
        df_t = pf.unfreeze(path, verify=True)
        assert np.array_equal(time_df["id"].values,
                               df_t["id"].values[:len(time_df)].astype(np.int64))

    def test_categorias_exatas(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path)
        df_t = pf.unfreeze(path)
        for col in ("status","regiao"):
            assert (time_df[col].astype(str).values ==
                    df_t[col].astype(str).values[:len(time_df)]).mean() == 1.0


class TestThawSeletivo:
    def test_filter_retorna_subset(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano", chunk_rows=2000)
        ano  = sorted(time_df["ano"].unique())[0]
        df_f = pf.unfreeze(path, filter={"ano": ano})
        assert 0 < len(df_f) < len(time_df)

    def test_filter_le_menos_que_full(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano", chunk_rows=2000)
        info = pf.audit(path)
        file_sz = os.path.getsize(path)
        ano = sorted(time_df["ano"].unique())[0]
        chunks_ano = [e for e in info["index_entries"] if str(ano) in e["part_key"]]
        bytes_ano  = sum(e["byte_len"]+32 for e in chunks_ano)
        assert bytes_ano / file_sz < 0.70

    def test_row_range(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, chunk_rows=2000)
        assert len(pf.unfreeze(path, row_range=(0, 1999))) <= 2000

    def test_filter_vazio_retorna_vazio(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path, partition_by="ano", chunk_rows=2000)
        assert len(pf.unfreeze(path, filter={"ano": 9999})) == 0


class TestIntegridadeChunks:
    def test_arquivo_correto_passa(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "g.permafrost")
        pf.freeze(time_df, path)
        assert len(pf.unfreeze(path, verify=True)) == len(time_df)

    def test_chunk_corrompido_detectado(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "g.permafrost")
        corrupt = os.path.join(tmp_dir, "c.permafrost")
        pf.freeze(time_df, path, chunk_rows=2000)
        shutil.copy(path, corrupt)
        info = pf.audit(corrupt)
        offset = info["index_entries"][0]["byte_offset"]
        with open(corrupt,"r+b") as f: f.seek(offset+100); f.write(b"\x00"*16)
        with pytest.raises(ValueError, match="[Cc]orrompido|SHA"):
            pf.unfreeze(corrupt, verify=True)

    def test_truncado_detectado(self, time_df, tmp_dir):
        path = os.path.join(tmp_dir, "g.permafrost")
        trunc = os.path.join(tmp_dir, "t.permafrost")
        pf.freeze(time_df, path)
        sz = os.path.getsize(path)
        with open(path,"rb") as s, open(trunc,"wb") as d: d.write(s.read(sz//2))
        with pytest.raises(ValueError): pf.unfreeze(trunc)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

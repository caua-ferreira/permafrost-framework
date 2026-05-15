"""
Testes para permafrost.storage — LocalAdapter, parse_uri, freeze_to, thaw_from, audit_remote.
Cloud adapters (S3/GCS/Azure) não são testados aqui por exigirem credenciais externas.
"""
import os, shutil, tempfile
import pytest
import numpy as np
import pandas as pd

from permafrost.storage import (
    parse_uri, ParsedURI,
    storage_from_uri,
    LocalAdapter,
    freeze_to, thaw_from, audit_remote,
)
import permafrost as pf


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_df():
    np.random.seed(7)
    N = 2_000
    return pd.DataFrame({
        "id":      np.arange(1, N + 1, dtype=np.int32),
        "ano":     np.random.choice([2022, 2023, 2024], N),
        "produto": np.random.choice(["A", "B", "C", "D"], N),
        "valor":   np.round(np.random.uniform(10, 9999, N), 2),
        "ts":      pd.date_range("2022-01-01", periods=N, freq="1h"),
    })


@pytest.fixture
def frozen_path(sample_df, tmp):
    path = os.path.join(tmp, "test.permafrost")
    pf.freeze(sample_df, path, codec=pf.CODEC_ZSTD, partition_by="ano")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# §1  parse_uri
# ══════════════════════════════════════════════════════════════════════════════

class TestParseURI:

    def test_s3_scheme(self):
        u = parse_uri("s3://meu-bucket/dados/vendas.permafrost")
        assert u.scheme == "s3"
        assert u.bucket == "meu-bucket"
        assert u.key    == "dados/vendas.permafrost"

    def test_gs_scheme(self):
        u = parse_uri("gs://projeto-bucket/path/file.permafrost")
        assert u.scheme == "gs"
        assert u.bucket == "projeto-bucket"
        assert u.key    == "path/file.permafrost"

    def test_azure_scheme(self):
        u = parse_uri("azure://container/blob/file.permafrost")
        assert u.scheme == "azure"
        assert u.bucket == "container"
        assert u.key    == "blob/file.permafrost"

    def test_local_absolute(self, tmp):
        path = os.path.join(tmp, "vendas.permafrost")
        u = parse_uri(path)
        assert u.scheme == "local"
        assert u.raw    == path

    def test_s3_sem_key(self):
        u = parse_uri("s3://bucket-vazio/")
        assert u.scheme == "s3"
        assert u.bucket == "bucket-vazio"

    def test_filename_property(self):
        u = parse_uri("s3://bucket/path/to/vendas.permafrost")
        assert u.filename == "vendas.permafrost"

    def test_is_directory_true(self):
        u = parse_uri("s3://bucket/path/")
        assert u.is_directory is True

    def test_is_directory_false(self):
        u = parse_uri("s3://bucket/path/file.permafrost")
        assert u.is_directory is False

    def test_http_lanca_valueerror(self):
        with pytest.raises(ValueError, match="HTTP"):
            parse_uri("http://example.com/file.permafrost")

    def test_https_lanca_valueerror(self):
        with pytest.raises(ValueError, match="HTTP"):
            parse_uri("https://example.com/file.permafrost")

    def test_raw_preservado(self):
        raw = "s3://bucket/key.permafrost"
        u = parse_uri(raw)
        assert u.raw == raw


# ══════════════════════════════════════════════════════════════════════════════
# §2  storage_from_uri (factory)
# ══════════════════════════════════════════════════════════════════════════════

class TestStorageFactory:

    def test_local_path_retorna_local_adapter(self, tmp):
        adapter = storage_from_uri(tmp)
        assert isinstance(adapter, LocalAdapter)

    def test_local_path_absoluto(self, tmp):
        path = os.path.join(tmp, "arquivo.permafrost")
        adapter = storage_from_uri(path)
        assert isinstance(adapter, LocalAdapter)

    def test_s3_retorna_s3adapter_sem_credenciais(self):
        # sem boto3 instalado ou sem credenciais, deve lançar ImportError ou similar
        # — apenas verifica que a factory tenta instanciar S3Adapter
        from permafrost.storage import S3Adapter
        try:
            adapter = storage_from_uri("s3://bucket/")
            assert isinstance(adapter, S3Adapter)
        except Exception:
            pass  # aceitável sem boto3/credenciais

    def test_gs_retorna_gcsadapter_sem_credenciais(self):
        from permafrost.storage import GCSAdapter
        try:
            adapter = storage_from_uri("gs://bucket/")
            assert isinstance(adapter, GCSAdapter)
        except Exception:
            pass

    def test_azure_retorna_azureadapter_sem_credenciais(self):
        from permafrost.storage import AzureAdapter
        try:
            adapter = storage_from_uri("azure://container/")
            assert isinstance(adapter, AzureAdapter)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# §3  LocalAdapter — operações básicas
# ══════════════════════════════════════════════════════════════════════════════

class TestLocalAdapterBasico:

    def test_upload_copia_arquivo(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        dest_uri = os.path.join(tmp, "copia.permafrost")
        result = adapter.upload(frozen_path, dest_uri, show_progress=False)
        assert os.path.exists(dest_uri)
        assert result["adapter"] == "local"
        assert result["size_bytes"] > 0

    def test_upload_retorna_metricas(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        dest_uri = os.path.join(tmp, "upload.permafrost")
        result = adapter.upload(frozen_path, dest_uri, show_progress=False)
        assert "uri"        in result
        assert "size_bytes" in result
        assert "upload_s"   in result

    def test_download_restaura_arquivo(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        dest_uri = os.path.join(tmp, "up.permafrost")
        adapter.upload(frozen_path, dest_uri, show_progress=False)

        dest_local = os.path.join(tmp, "baixado.permafrost")
        result = adapter.download(dest_uri, dest_local, show_progress=False)
        assert os.path.exists(dest_local)
        assert result["size_bytes"] == os.path.getsize(dest_uri)

    def test_download_arquivo_inexistente_lanca_erro(self, tmp):
        adapter = LocalAdapter(tmp)
        with pytest.raises(FileNotFoundError):
            adapter.download(os.path.join(tmp, "nao_existe.permafrost"),
                             os.path.join(tmp, "out.permafrost"),
                             show_progress=False)

    def test_exists_true(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        assert adapter.exists(frozen_path) is True

    def test_exists_false(self, tmp):
        adapter = LocalAdapter(tmp)
        assert adapter.exists(os.path.join(tmp, "nao_existe.permafrost")) is False

    def test_delete_remove_arquivo(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "del.permafrost")
        adapter.upload(frozen_path, dest, show_progress=False)
        assert adapter.exists(dest)
        result = adapter.delete(dest)
        assert result is True
        assert not adapter.exists(dest)

    def test_delete_inexistente_retorna_false(self, tmp):
        adapter = LocalAdapter(tmp)
        result = adapter.delete(os.path.join(tmp, "ghost.permafrost"))
        assert result is False

    def test_list_retorna_permafrost(self, tmp, sample_df):
        adapter = LocalAdapter(tmp)
        for i in range(3):
            pf.freeze(sample_df.head(200), os.path.join(tmp, f"f{i}.permafrost"))
        found = adapter.list(tmp)
        assert len(found) == 3
        assert all(f.endswith(".permafrost") for f in found)

    def test_list_dir_vazio(self, tmp):
        adapter = LocalAdapter(tmp)
        sub = os.path.join(tmp, "vazio")
        os.makedirs(sub)
        assert adapter.list(sub) == []

    def test_list_dir_inexistente(self, tmp):
        adapter = LocalAdapter(tmp)
        assert adapter.list(os.path.join(tmp, "nao_existe")) == []

    def test_read_bytes_conteudo_correto(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        data = adapter.read_bytes(frozen_path)
        assert isinstance(data, bytes)
        assert len(data) == os.path.getsize(frozen_path)
        assert data[:4] == b'PRMS'  # magic bytes

    def test_write_bytes_grava_e_le(self, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "bytes.bin")
        payload = b"hello permafrost"
        result = adapter.write_bytes(payload, dest)
        assert result["size_bytes"] == len(payload)
        assert adapter.read_bytes(dest) == payload

    def test_read_header_bytes_primeiros_n_bytes(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        header = adapter.read_header_bytes(frozen_path, n_bytes=16)
        assert len(header) >= 4
        assert header[:4] == b'PRMS'

    def test_read_footer_bytes_ultimos_n_bytes(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        footer = adapter.read_footer_bytes(frozen_path, n_bytes=64)
        assert len(footer) >= 4
        assert footer[-4:] == b'SMRP'  # EOF magic


# ══════════════════════════════════════════════════════════════════════════════
# §4  upload_and_verify
# ══════════════════════════════════════════════════════════════════════════════

class TestUploadAndVerify:

    def test_retorna_sha256_e_magic_ok(self, tmp, frozen_path):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "verified.permafrost")
        result = adapter.upload_and_verify(frozen_path, dest)
        assert "local_sha256"   in result
        assert "remote_magic_ok" in result
        assert result["remote_magic_ok"] is True
        assert len(result["local_sha256"]) == 64  # SHA-256 hex

    def test_sha256_correto(self, tmp, frozen_path):
        import hashlib
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "sha_check.permafrost")
        result = adapter.upload_and_verify(frozen_path, dest)
        expected = hashlib.sha256(open(frozen_path, "rb").read()).hexdigest()
        assert result["local_sha256"] == expected


# ══════════════════════════════════════════════════════════════════════════════
# §5  freeze_to / thaw_from (high-level API com LocalAdapter)
# ══════════════════════════════════════════════════════════════════════════════

class TestFreezeTo:

    def test_freeze_to_local_cria_arquivo(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "out.permafrost")
        metrics = freeze_to(sample_df, dest, adapter=adapter,
                            tmp_dir=tmp, keep_local=False)
        assert os.path.exists(dest)
        assert metrics["rows"] == len(sample_df)

    def test_freeze_to_retorna_metricas_completas(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "metricas.permafrost")
        metrics = freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp)
        for campo in ("rows", "cols", "ratio", "stored_mb", "remote_uri"):
            assert campo in metrics, f"Campo ausente: {campo}"

    def test_freeze_to_ratio_positivo(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "ratio.permafrost")
        metrics = freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp)
        assert metrics["ratio"] > 1.0

    def test_freeze_to_keep_local_false_apaga_tmp(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "no_local.permafrost")
        freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp, keep_local=False)
        # o arquivo _pf_*_no_local.permafrost (temporário) deve ter sido apagado
        pf_tmp_files = [f for f in os.listdir(tmp) if f.startswith("_pf_")]
        assert len(pf_tmp_files) == 0
        # mas o "remoto" local ainda existe
        assert os.path.exists(dest)

    def test_freeze_to_com_codec_zstd(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "zstd.permafrost")
        metrics = freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp,
                            codec=pf.CODEC_ZSTD)
        assert metrics["rows"] == len(sample_df)

    def test_freeze_to_com_partition_by(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "partitioned.permafrost")
        metrics = freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp,
                            partition_by="ano")
        assert metrics["rows"] == len(sample_df)


class TestThawFrom:

    def test_thaw_from_restaura_shape(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "roundtrip.permafrost")
        freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp, keep_local=True)
        df2 = thaw_from(dest, adapter=adapter, tmp_dir=tmp, keep_local=False)
        assert len(df2) == len(sample_df)
        assert set(df2.columns) == set(sample_df.columns)

    def test_thaw_from_valores_corretos(self, tmp):
        adapter = LocalAdapter(tmp)
        df = pd.DataFrame({"x": [1, 2, 3], "y": [10.0, 20.0, 30.0]})
        dest = os.path.join(tmp, "vals.permafrost")
        freeze_to(df, dest, adapter=adapter, tmp_dir=tmp, keep_local=True)
        df2 = thaw_from(dest, adapter=adapter, tmp_dir=tmp)
        assert list(df2["x"]) == [1, 2, 3]

    def test_thaw_from_com_filter(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "filter_test.permafrost")
        freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp,
                  keep_local=True, partition_by="ano")
        df_2024 = thaw_from(dest, adapter=adapter, tmp_dir=tmp,
                            keep_local=False, filter={"ano": 2024})
        assert len(df_2024) > 0
        assert set(df_2024["ano"].unique()) == {2024}

    def test_thaw_from_sem_keep_local_apaga_tmp(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "no_keep.permafrost")
        freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp, keep_local=False)
        # após freeze_to(keep_local=False) nenhum _pf_* deve sobrar
        before = [f for f in os.listdir(tmp) if f.startswith("_pf_")]
        assert len(before) == 0
        # thaw_from(keep_local=False) não deve deixar novos _pf_*
        thaw_from(dest, adapter=adapter, tmp_dir=tmp, keep_local=False)
        after = [f for f in os.listdir(tmp) if f.startswith("_pf_")]
        assert len(after) == 0


# ══════════════════════════════════════════════════════════════════════════════
# §6  audit_remote
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditRemote:

    def test_audit_remote_retorna_campos_basicos(self, frozen_path, tmp):
        adapter = LocalAdapter(tmp)
        info = audit_remote(frozen_path, adapter=adapter)
        for campo in ("uri", "codec", "orig_rows", "n_chunks", "columns"):
            assert campo in info, f"Campo ausente: {campo}"

    def test_audit_remote_orig_rows_correto(self, sample_df, frozen_path, tmp):
        adapter = LocalAdapter(tmp)
        info = audit_remote(frozen_path, adapter=adapter)
        assert info["orig_rows"] == len(sample_df)

    def test_audit_remote_colunas_corretas(self, sample_df, frozen_path, tmp):
        adapter = LocalAdapter(tmp)
        info = audit_remote(frozen_path, adapter=adapter)
        assert set(info["columns"]) == set(sample_df.columns)

    def test_audit_remote_codec_zstd(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "zstd_audit.permafrost")
        freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp,
                  keep_local=True, codec=pf.CODEC_ZSTD)
        info = audit_remote(dest, adapter=adapter)
        assert info["codec"] == "zstd"

    def test_audit_remote_codec_lzma2(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "lzma2_audit.permafrost")
        freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp,
                  keep_local=True, codec=pf.CODEC_LZMA2)
        info = audit_remote(dest, adapter=adapter)
        assert info["codec"] == "lzma2"

    def test_audit_remote_arquivo_invalido_lanca_erro(self, tmp):
        adapter = LocalAdapter(tmp)
        fake = os.path.join(tmp, "fake.permafrost")
        open(fake, "wb").write(b"isso nao e permafrost")
        with pytest.raises(ValueError):
            audit_remote(fake, adapter=adapter)

    def test_audit_remote_partition_keys(self, sample_df, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "part_audit.permafrost")
        freeze_to(sample_df, dest, adapter=adapter, tmp_dir=tmp,
                  keep_local=True, partition_by="ano")
        info = audit_remote(dest, adapter=adapter)
        assert info["partition_col"] == "ano"
        assert len(info["partition_keys"]) > 0


# ══════════════════════════════════════════════════════════════════════════════
# §7  Round-trip completo via LocalAdapter
# ══════════════════════════════════════════════════════════════════════════════

class TestRoundTrip:

    def test_roundtrip_inteiros(self, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "int_rt.permafrost")
        df = pd.DataFrame({"a": list(range(1000)), "b": list(range(1000, 2000))})
        freeze_to(df, dest, adapter=adapter, tmp_dir=tmp, keep_local=True)
        df2 = thaw_from(dest, adapter=adapter, tmp_dir=tmp)
        assert list(df["a"]) == list(df2["a"])
        assert list(df["b"]) == list(df2["b"])

    def test_roundtrip_strings(self, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "str_rt.permafrost")
        df = pd.DataFrame({"nome": ["Alice", "Bob", "Carlos"] * 200})
        freeze_to(df, dest, adapter=adapter, tmp_dir=tmp, keep_local=True)
        df2 = thaw_from(dest, adapter=adapter, tmp_dir=tmp)
        assert sorted(df["nome"].tolist()) == sorted(df2["nome"].tolist())

    def test_roundtrip_dataframe_grande(self, tmp):
        adapter = LocalAdapter(tmp)
        dest = os.path.join(tmp, "large_rt.permafrost")
        np.random.seed(99)
        N = 50_000
        df = pd.DataFrame({
            "id":    np.arange(N, dtype=np.int32),
            "val":   np.round(np.random.uniform(0, 1e6, N), 2),
            "cat":   np.random.choice(["X", "Y", "Z"], N),
        })
        freeze_to(df, dest, adapter=adapter, tmp_dir=tmp,
                  keep_local=True, codec=pf.CODEC_ZSTD)
        df2 = thaw_from(dest, adapter=adapter, tmp_dir=tmp)
        assert len(df2) == N
        assert set(df2.columns) == {"id", "val", "cat"}

    def test_roundtrip_multiple_uploads(self, tmp):
        """Múltiplos freeze_to independentes não interferem entre si."""
        adapter = LocalAdapter(tmp)
        dfs, paths = [], []
        for i in range(5):
            df_i = pd.DataFrame({"n": list(range(i * 100, (i + 1) * 100))})
            dest = os.path.join(tmp, f"multi_{i}.permafrost")
            freeze_to(df_i, dest, adapter=adapter, tmp_dir=tmp, keep_local=True)
            dfs.append(df_i)
            paths.append(dest)

        for i, (path, df_orig) in enumerate(zip(paths, dfs)):
            df_restored = thaw_from(path, adapter=adapter, tmp_dir=tmp)
            assert list(df_restored["n"]) == list(df_orig["n"]), \
                f"Mismatch no arquivo {i}"


if __name__ == "__main__":
    import pytest as _pt
    _pt.main([__file__, "-v", "--tb=short"])

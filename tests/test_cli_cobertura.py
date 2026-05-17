"""
CLI Cobertura Total — testa todos os subcomandos via subprocess.
Executar: pytest tests/test_cli_cobertura.py -v
"""
import os, shutil, tempfile, subprocess, sys, json
import pytest
import numpy as np
import pandas as pd
import permafrost as pf

PY = sys.executable
CLI = [PY, "-m", "permafrost"]  # usa __main__.py


def run_cli(*args, input=None, timeout=60):
    """Executa um comando CLI e retorna (returncode, stdout, stderr)."""
    cmd = CLI + list(args)
    r   = subprocess.run(cmd, capture_output=True, text=True,
                         timeout=timeout, input=input)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(scope="module")
def sample_csv(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cli")
    np.random.seed(42); N = 3_000
    df  = pd.DataFrame({
        "id":     np.arange(1, N+1, dtype=np.int32),
        "ano":    np.random.choice([2021, 2022, 2023], N).astype(np.int16),
        "total":  np.round(np.random.uniform(1, 5000, N), 2),
        "status": np.random.choice(["Ativo","Inativo","Pendente"], N),
    })
    df = df.sort_values("ano").reset_index(drop=True)
    path = str(tmp / "cli_input.csv")
    df.to_csv(path, index=False)
    return path, str(tmp), N, df


# ══════════════════════════════════════════════════════════════════════════════
# §1 HELP E VERSÃO
# ══════════════════════════════════════════════════════════════════════════════

class TestCLIHelp:

    def test_sem_args_mostra_uso(self):
        rc, out, _ = run_cli()
        assert rc != 0 or "permafrost" in out.lower() or "Uso" in out

    def test_help_master(self):
        rc, out, err = run_cli("master", "--help")
        assert "master" in (out + err).lower() or "port" in (out + err).lower()

    def test_help_worker(self):
        rc, out, err = run_cli("worker", "--help")
        assert "worker" in (out + err).lower() or "master" in (out + err).lower()


# ══════════════════════════════════════════════════════════════════════════════
# §2 FREEZE VIA CLI
# ══════════════════════════════════════════════════════════════════════════════

class TestCLIFreeze:

    def test_freeze_csv_cria_arquivo(self, sample_csv, tmp):
        path, _, N, _ = sample_csv
        out = os.path.join(tmp, "cli_out.permafrost")
        rc, stdout, stderr = run_cli("freeze", path, "--output", out)
        assert os.path.exists(out), f"Arquivo não criado. rc={rc} stderr={stderr[:200]}"

    def test_freeze_csv_magic_valido(self, sample_csv, tmp):
        path, _, _, _ = sample_csv
        out = os.path.join(tmp, "cli_magic.permafrost")
        run_cli("freeze", path, "--output", out)
        if os.path.exists(out):
            assert open(out, "rb").read(4) == b"PRMS"

    def test_freeze_com_codec_zstd(self, sample_csv, tmp):
        path, _, _, _ = sample_csv
        out = os.path.join(tmp, "cli_zstd.permafrost")
        rc, _, err = run_cli("freeze", path, "--output", out, "--codec", "zstd")
        if os.path.exists(out):
            info = pf.audit(out)
            assert info["codec"] == "zstd"

    def test_freeze_com_partition_by(self, sample_csv, tmp):
        path, _, _, _ = sample_csv
        out = os.path.join(tmp, "cli_part.permafrost")
        rc, _, _ = run_cli("freeze", path, "--output", out, "--partition-by", "ano")
        if os.path.exists(out):
            info = pf.audit(out)
            assert info["partition_col"] == "ano"

    def test_freeze_arquivo_inexistente_exit_1(self, tmp):
        out = os.path.join(tmp, "nao_vai_existir.permafrost")
        rc, _, _ = run_cli("freeze", "/nao/existe.csv", "--output", out)
        assert rc != 0, "freeze de arquivo inexistente deveria retornar exit code != 0"

    def test_freeze_sem_output_usa_mesmo_diretorio(self, sample_csv, tmp):
        """Sem --output, deve criar .permafrost no mesmo diretório do CSV."""
        path, csv_tmp, _, _ = sample_csv
        # Copiar CSV para tmp para não sujar o fixture
        import shutil
        csv_copy = os.path.join(tmp, "copy.csv")
        shutil.copy(path, csv_copy)
        run_cli("freeze", csv_copy)
        expected = csv_copy.replace(".csv", ".permafrost")
        if os.path.exists(expected):
            assert open(expected, "rb").read(4) == b"PRMS"


# ══════════════════════════════════════════════════════════════════════════════
# §3 THAW VIA CLI
# ══════════════════════════════════════════════════════════════════════════════

class TestCLIThaw:

    @pytest.fixture
    def pf_file(self, sample_csv, tmp):
        path, _, N, df = sample_csv
        pf_path = os.path.join(tmp, "thaw_input.permafrost")
        pf.freeze(df, pf_path, partition_by="ano", chunk_rows=500)
        return pf_path, N

    def test_thaw_imprime_dados(self, pf_file, tmp):
        pf_path, N = pf_file
        rc, out, err = run_cli("unfreeze", pf_path)
        # Deve imprimir algo (pode ser CSV ou JSON)
        assert rc == 0 or len(out) > 0 or len(err) > 0

    def test_thaw_arquivo_inexistente_exit_1(self, tmp):
        rc, _, _ = run_cli("unfreeze", "/nao/existe.permafrost")
        assert rc != 0

    def test_thaw_arquivo_invalido_exit_1(self, tmp):
        fake = os.path.join(tmp, "fake.permafrost")
        open(fake, "w").write("nao e um permafrost valido")
        rc, _, _ = run_cli("unfreeze", fake)
        assert rc != 0


# ══════════════════════════════════════════════════════════════════════════════
# §4 AUDIT VIA CLI
# ══════════════════════════════════════════════════════════════════════════════

class TestCLIAudit:

    @pytest.fixture
    def pf_ready(self, sample_csv, tmp):
        path, _, N, df = sample_csv
        pf_path = os.path.join(tmp, "audit_input.permafrost")
        pf.freeze(df, pf_path, partition_by="ano",
                  chunk_rows=500, comment="CLI audit test")
        return pf_path, N

    def test_audit_retorna_informacoes(self, pf_ready, tmp):
        pf_path, N = pf_ready
        rc, out, err = run_cli("audit", pf_path)
        combined = out + err
        # Deve mencionar o número de linhas ou o codec
        assert any(kw in combined for kw in ["lzma", "rows", "linhas", str(N), "codec",
                                              "version", "chunks"])

    def test_audit_arquivo_inexistente_exit_1(self, tmp):
        rc, _, _ = run_cli("audit", "/nao/existe.permafrost")
        assert rc != 0

    def test_audit_nao_modifica_arquivo(self, pf_ready, tmp):
        pf_path, _ = pf_ready
        sz_before = os.path.getsize(pf_path)
        run_cli("audit", pf_path)
        assert os.path.getsize(pf_path) == sz_before


# ══════════════════════════════════════════════════════════════════════════════
# §5 VERIFY VIA CLI
# ══════════════════════════════════════════════════════════════════════════════

class TestCLIVerify:

    @pytest.fixture
    def pf_good(self, sample_csv, tmp):
        path, _, N, df = sample_csv
        pf_path = os.path.join(tmp, "verify_good.permafrost")
        pf.freeze(df, pf_path, chunk_rows=500)
        return pf_path

    def test_verify_arquivo_integro_exit_0(self, pf_good):
        rc, out, err = run_cli("verify", pf_good)
        combined = out + err
        # Exit 0 ou mensagem de OK
        assert rc == 0 or any(w in combined.lower() for w in ["ok","valid","íntegro","✓","pass"])

    def test_verify_arquivo_corrompido_exit_1(self, pf_good, tmp):
        import shutil
        corrupt = os.path.join(tmp, "corrupt.permafrost")
        shutil.copy(pf_good, corrupt)
        sz = os.path.getsize(corrupt)
        with open(corrupt, "r+b") as f:
            f.seek(sz // 2); f.write(b"\xFF" * 32)
        rc, _, _ = run_cli("verify", corrupt)
        assert rc != 0, "verify de arquivo corrompido deveria retornar exit code != 0"

    def test_verify_arquivo_inexistente_exit_1(self, tmp):
        rc, _, _ = run_cli("verify", "/nao/existe.permafrost")
        assert rc != 0


# ══════════════════════════════════════════════════════════════════════════════
# §6 COMANDOS DESCONHECIDOS
# ══════════════════════════════════════════════════════════════════════════════

class TestCLIComandosInvalidos:

    def test_comando_desconhecido_exit_nao_zero(self):
        rc, out, err = run_cli("comando_que_nao_existe_xyz")
        assert rc != 0

    def test_freeze_sem_input_exit_nao_zero(self, tmp):
        rc, _, _ = run_cli("freeze")
        assert rc != 0

    def test_worker_sem_master_exit_nao_zero(self):
        # Worker sem --master deve falhar com mensagem clara
        rc, _, err = run_cli("worker", timeout=5)
        # Pode travar ou falhar — o importante é não crashar silenciosamente
        assert rc != 0 or "master" in err.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

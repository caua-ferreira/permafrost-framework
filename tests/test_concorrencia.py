"""
Acesso Concorrente — threading e multiprocessing em leitura e escrita.
Cobre: leitura paralela, audit simultâneo, catalog concorrente.
Executar: pytest tests/test_concorrencia.py -v
"""
import os, shutil, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import pytest
import numpy as np
import pandas as pd
import permafrost as pf


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(scope="module")
def shared_file(tmp_path_factory):
    """Arquivo .permafrost compartilhado entre todos os testes de concorrência."""
    tmp  = tmp_path_factory.mktemp("concurrent")
    np.random.seed(42); N = 20_000
    df = pd.DataFrame({
        "id":     np.arange(1, N+1, dtype=np.int32),
        "ano":    pd.date_range("2020-01-01", periods=N, freq="1h").year.astype(np.int16),
        "regiao": np.random.choice(["Norte","Sul","Leste","Oeste"], N),
        "total":  np.round(np.random.uniform(1, 50000, N), 2),
        "status": np.random.choice(["Ativo","Inativo","Pendente"], N),
    }).sort_values("ano").reset_index(drop=True)
    path = str(tmp / "shared.permafrost")
    pf.freeze(df, path, codec=pf.CODEC_LZMA2, partition_by="ano", chunk_rows=2000)
    return path, df, N


# ══════════════════════════════════════════════════════════════════════════════
# §1 LEITURA CONCORRENTE — THAW PARALELO
# ══════════════════════════════════════════════════════════════════════════════

class TestThawConcorrente:

    def test_10_threads_thaw_simultaneo(self, shared_file):
        """10 threads lendo o mesmo arquivo ao mesmo tempo."""
        path, df, N = shared_file
        resultados = {}
        erros      = []

        def worker(tid):
            try:
                df_b = pf.unfreeze(path, verify=True)
                resultados[tid] = len(df_b)
            except Exception as e:
                erros.append((tid, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=60)

        assert len(erros) == 0, f"Erros em threads: {erros}"
        assert len(resultados) == 10
        assert all(v == N for v in resultados.values()), \
            f"Linhas inconsistentes: {set(resultados.values())}"

    def test_10_threads_thaw_com_filter(self, shared_file):
        """10 threads com filtros diferentes simultâneos."""
        path, df, N = shared_file
        anos    = sorted(df["ano"].unique().tolist())
        results = {}; erros = []

        def worker(ano):
            try:
                df_b = pf.unfreeze(path, filter={"ano": ano}, verify=True)
                results[ano] = len(df_b)
            except Exception as e:
                erros.append((ano, str(e)))

        with ThreadPoolExecutor(max_workers=len(anos)) as ex:
            futs = {ex.submit(worker, a): a for a in anos}
            for f in as_completed(futs, timeout=60): pass

        assert len(erros) == 0, f"Erros: {erros}"
        total = sum(results.values())
        assert total >= N * 0.99, f"Total filtros ({total}) < N ({N})"

    def test_5_threads_audit_simultaneo(self, shared_file):
        """5 threads fazendo audit() ao mesmo tempo."""
        path, _, N = shared_file
        results = {}; erros = []

        def worker(tid):
            try:
                info = pf.audit(path)
                results[tid] = info["orig_rows"]
            except Exception as e:
                erros.append(str(e))

        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = [ex.submit(worker, i) for i in range(5)]
            for f in as_completed(futs, timeout=30): pass

        assert len(erros) == 0, f"Erros de audit: {erros}"
        assert all(v == N for v in results.values())

    def test_thaw_full_e_filter_simultaneos(self, shared_file):
        """thaw() completo e thaw(filter=) simultâneos no mesmo arquivo."""
        path, df, N = shared_file
        r_full = {}; r_filter = {}; erros = []

        def do_full():
            try:
                df_b = pf.unfreeze(path, verify=True)
                r_full["rows"] = len(df_b)
            except Exception as e:
                erros.append(f"full: {e}")

        def do_filter():
            try:
                ano  = sorted(df["ano"].unique())[0]
                df_b = pf.unfreeze(path, filter={"ano": ano})
                r_filter["rows"] = len(df_b)
            except Exception as e:
                erros.append(f"filter: {e}")

        threads = [threading.Thread(target=do_full),
                   threading.Thread(target=do_filter)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=30)

        assert len(erros) == 0, f"Erros: {erros}"
        assert r_full.get("rows") == N
        assert r_filter.get("rows", 0) > 0

    def test_leituras_retornam_mesmo_dado(self, shared_file):
        """Verificar que todas as threads retornam exatamente os mesmos dados."""
        path, df, N = shared_file
        all_sums = []; erros = []

        def worker():
            try:
                df_b = pf.unfreeze(path)
                all_sums.append(float(df_b["total"].sum()))
            except Exception as e:
                erros.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=60)

        assert len(erros) == 0
        assert len(all_sums) == 8
        # Todas as somas devem ser idênticas
        assert max(all_sums) - min(all_sums) < 0.01, \
            f"Somas divergem: {min(all_sums):.2f} vs {max(all_sums):.2f}"


# ══════════════════════════════════════════════════════════════════════════════
# §2 ESCRITA CONCORRENTE — FREEZE PARALELO
# ══════════════════════════════════════════════════════════════════════════════

class TestFreezeConcorrente:

    def test_3_threads_freeze_arquivos_diferentes(self, tmp):
        """3 threads fazendo freeze em arquivos diferentes simultaneamente."""
        erros = []; resultados = {}

        def worker(tid):
            try:
                np.random.seed(tid)
                N = 5_000
                df = pd.DataFrame({
                    "id":  np.arange(1, N+1, dtype=np.int32),
                    "v":   np.round(np.random.uniform(1, 1000, N), 2),
                    "cat": np.random.choice(["A","B","C"], N),
                })
                path = os.path.join(tmp, f"thread_{tid}.permafrost")
                m = pf.freeze(df, path, codec=pf.CODEC_LZMA2)
                resultados[tid] = m["rows"]
            except Exception as e:
                erros.append(f"t{tid}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=60)

        assert len(erros) == 0, f"Erros: {erros}"
        assert len(resultados) == 3
        assert all(v == 5_000 for v in resultados.values())

        # Verificar que todos os arquivos são válidos
        for i in range(3):
            path = os.path.join(tmp, f"thread_{i}.permafrost")
            info = pf.audit(path)
            assert info["orig_rows"] == 5_000

    def test_freeze_e_thaw_paralelos_sem_interferencia(self, tmp):
        """freeze em arquivo A e thaw em arquivo B simultaneamente."""
        np.random.seed(42); N = 10_000
        df = pd.DataFrame({
            "id": np.arange(1, N+1, dtype=np.int32),
            "v":  np.round(np.random.uniform(1, 1000, N), 2),
        })

        # Criar arquivo B previamente
        path_b = os.path.join(tmp, "b.permafrost")
        pf.freeze(df, path_b)

        erros = []; res_freeze = {}; res_thaw = {}

        def do_freeze():
            try:
                path_a = os.path.join(tmp, "a.permafrost")
                m = pf.freeze(df, path_a)
                res_freeze["rows"] = m["rows"]
            except Exception as e:
                erros.append(f"freeze: {e}")

        def do_thaw():
            try:
                df_b = pf.unfreeze(path_b, verify=True)
                res_thaw["rows"] = len(df_b)
            except Exception as e:
                erros.append(f"unfreeze: {e}")

        t1 = threading.Thread(target=do_freeze)
        t2 = threading.Thread(target=do_thaw)
        t1.start(); t2.start()
        t1.join(timeout=60); t2.join(timeout=60)

        assert len(erros) == 0, f"Erros: {erros}"
        assert res_freeze.get("rows") == N
        assert res_thaw.get("rows") == N

    def test_stream_freeze_sequencial_por_thread(self, tmp):
        """Cada thread faz freeze_stream independente — sem compartilhamento."""
        erros = []; results = {}

        def worker(tid):
            try:
                N = 30_000; BLOCK = 10_000
                np.random.seed(tid)

                def gen():
                    for s in range(0, N, BLOCK):
                        nb = min(BLOCK, N-s); np.random.seed(tid*100+s)
                        yield pd.DataFrame({
                            "id": np.arange(s+1, s+nb+1, dtype=np.int32),
                            "v":  np.round(np.random.uniform(1,100,nb), 2),
                        })

                path = os.path.join(tmp, f"stream_{tid}.permafrost")
                m = pf.freeze_stream(gen(), path)
                results[tid] = m["rows"]
            except Exception as e:
                erros.append(f"t{tid}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=120)

        assert len(erros) == 0, f"Erros: {erros}"
        assert all(v == 30_000 for v in results.values())


# ══════════════════════════════════════════════════════════════════════════════
# §3 CATALOG CONCORRENTE
# ══════════════════════════════════════════════════════════════════════════════

class TestCatalogConcorrente:

    def test_register_concorrente_arquivos_diferentes(self, tmp):
        """Cada thread registra arquivos em seu próprio catalog independente.
        
        Nota: DuckDB em modo :memory: não é compartilhável entre threads.
        Para uso concurrent real, cada processo deve ter sua própria conexão.
        Este teste valida que o padrão correto (1 cat por thread) funciona.
        """
        n_threads = 5; n_files_per_thread = 4
        all_results = {}; erros = []

        def worker(tid):
            try:
                cat = pf.PermafrostCatalog(":memory:")
                for i in range(n_files_per_thread):
                    np.random.seed(tid * 100 + i)
                    df = pd.DataFrame({"id": range(100), "v": np.random.rand(100)})
                    p  = os.path.join(tmp, f"t{tid}_f{i}.permafrost")
                    pf.freeze(df, p)
                    cat.register(p)
                all_results[tid] = cat.stats()["total_datasets"]
            except Exception as e:
                erros.append(f"t{tid}: {e}")

        with ThreadPoolExecutor(max_workers=n_threads) as ex:
            futs = [ex.submit(worker, i) for i in range(n_threads)]
            for f in as_completed(futs, timeout=60): pass

        assert len(erros) == 0, f"Erros: {erros}"
        assert all(v == n_files_per_thread for v in all_results.values()), \
            f"Resultados: {all_results}"

    def test_search_concorrente(self, tmp):
        """5 threads cada uma com seu catalog independente — padrão correto."""
        # Criar arquivos compartilhados
        paths = []
        for i in range(10):
            np.random.seed(i)
            df = pd.DataFrame({"id": range(200), "v": np.random.rand(200)})
            p  = os.path.join(tmp, f"s{i}.permafrost")
            pf.freeze(df, p)
            paths.append(p)

        results = {}; erros = []

        def worker(tid):
            try:
                # Cada thread tem seu catalog próprio (correto para DuckDB)
                cat = pf.PermafrostCatalog(":memory:")
                for p in paths:
                    cat.register(p)
                df_s = cat.search()
                results[tid] = len(df_s)
            except Exception as e:
                erros.append(f"t{tid}: {e}")

        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = [ex.submit(worker, i) for i in range(5)]
            for f in as_completed(futs, timeout=60): pass

        assert len(erros) == 0, f"Erros: {erros}"
        assert all(v == 10 for v in results.values())

    def test_integrity_check_concorrente(self, tmp):
        """3 threads fazendo integrity_check com seus próprios catalogs."""
        paths = []
        for i in range(5):
            df = pd.DataFrame({"id": range(500), "v": range(500)})
            p  = os.path.join(tmp, f"ic{i}.permafrost")
            pf.freeze(df, p)
            paths.append(p)

        erros = []; results = {}

        def worker(tid):
            try:
                cat = pf.PermafrostCatalog(":memory:")
                for p in paths:
                    cat.register(p)
                ic = cat.integrity_check()
                results[tid] = (ic["status"] == "OK").all()
            except Exception as e:
                erros.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=60)

        assert len(erros) == 0, f"Erros: {erros}"
        assert all(results.values())


# ══════════════════════════════════════════════════════════════════════════════
# §4 CONSISTÊNCIA SOB CARGA
# ══════════════════════════════════════════════════════════════════════════════

class TestConsistenciaSOBCarga:

    def test_100_thaws_consecutivos_mesmo_resultado(self, shared_file):
        """100 thaws consecutivos devem retornar sempre o mesmo resultado."""
        path, _, N = shared_file
        sums = set()
        for _ in range(100):
            df_b = pf.unfreeze(path, verify=False)   # verify=False para velocidade
            sums.add(round(float(df_b["total"].sum()), 2))
        assert len(sums) == 1, f"Resultados inconsistentes: {sums}"

    def test_audit_100_vezes_sem_degradacao(self, shared_file):
        """100 audits devem retornar sempre o mesmo resultado."""
        path, _, N = shared_file
        row_counts = set()
        t0 = time.time()
        for _ in range(100):
            info = pf.audit(path)
            row_counts.add(info["orig_rows"])
        elapsed = time.time() - t0
        assert len(row_counts) == 1
        assert elapsed < 5.0, f"100 audits demoraram {elapsed:.2f}s (limite: 5s)"

    def test_thaw_iter_100_batches_sem_dados_perdidos(self, tmp):
        """peek com 100 batches pequenos cobre todos os dados."""
        N = 100_000; CHUNK = 1_000
        def gen():
            for s in range(0, N, 10_000):
                nb = min(10_000, N-s)
                yield pd.DataFrame({
                    "id": np.arange(s+1, s+nb+1, dtype=np.int32),
                    "v":  np.arange(s+1, s+nb+1, dtype=float),
                })
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze_stream(gen(), path)

        total = 0; sum_v = 0.0
        for batch in pf.peek(path, batch_size=CHUNK):
            total += len(batch)
            sum_v += float(batch["v"].sum())

        assert total == N
        expected_sum = sum(range(1, N+1))
        assert abs(sum_v - expected_sum) < 1.0, \
            f"Soma diverge: {sum_v:.0f} vs {expected_sum}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

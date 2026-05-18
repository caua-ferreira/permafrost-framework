"""
Testes do PermafrostCluster — Master + Worker + Client.
Executar: pytest tests/test_cluster.py -v
"""
import os, socket, time, threading
import pytest
import numpy as np
import pandas as pd
import uvicorn
import permafrost as pf
from permafrost import PermafrostMaster, PermafrostWorker, PermafrostClient


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    """Sobe Master + 2 Workers. Retorna (client, tmp_dir)."""
    np.random.seed(42)
    mport = _free_port()
    master_url = f"http://127.0.0.1:{mport}"
    master = PermafrostMaster(host="127.0.0.1", port=mport)
    workers = [
        PermafrostWorker(master_url,
                         host="127.0.0.1", port=_free_port(),
                         worker_id=f"test-w{i+1:02d}")
        for i in range(2)
    ]
    threading.Thread(
        target=lambda: uvicorn.run(master.app, host="127.0.0.1",
                                   port=mport, log_level="error"), daemon=True).start()
    for w in workers:
        threading.Thread(
            target=lambda ww=w: uvicorn.run(ww.app, host="127.0.0.1",
                                            port=ww.port, log_level="error"), daemon=True).start()
    time.sleep(2.5)
    for w in workers:
        try: w.register()
        except: pass
    time.sleep(0.5)
    client = PermafrostClient(master_url)
    tmp = tmp_path_factory.mktemp("cluster_data")
    yield client, str(tmp)


@pytest.fixture(scope="module")
def csv_file(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("csv")
    N = 10_000
    np.random.seed(42)
    df = pd.DataFrame({
        "id":     np.arange(1, N+1, dtype=np.int32),
        "total":  np.round(np.random.uniform(1, 5000, N), 2),
        "status": np.random.choice(["Ativo","Inativo","Pendente"], N),
        "regiao": np.random.choice(["Norte","Sul","Leste"], N),
    })
    path = str(tmp / "input.csv")
    df.to_csv(path, index=False)
    return path, str(tmp), N


class TestMasterHealth:
    def test_health_ok(self, cluster):
        client, _ = cluster
        assert client.health()["status"] == "ok"

    def test_workers_registrados(self, cluster):
        client, _ = cluster
        assert client.health()["workers"] >= 2

    def test_workers_idle(self, cluster):
        client, _ = cluster
        assert client.health()["idle_workers"] >= 2

    def test_list_workers(self, cluster):
        client, _ = cluster
        ws = client.list_workers()
        assert len(ws) >= 2
        assert all("worker_id" in w and "status" in w for w in ws)


class TestJobLifecycle:
    def test_submit_job(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, _ = csv_file
        job_id = client.freeze(path, f"{tmp}/t1.permafrost", chunk_rows=3000)
        assert job_id and len(job_id) > 0

    def test_job_completa(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, _ = csv_file
        job_id = client.freeze(path, f"{tmp}/t2.permafrost", chunk_rows=3000)
        final  = client.wait(job_id, timeout=60)
        assert final["status"] == "done"

    def test_todas_tasks_done(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        job_id = client.freeze(path, f"{tmp}/t3.permafrost", chunk_rows=2500)
        final  = client.wait(job_id, timeout=60)
        tasks  = final.get("tasks", [])
        assert len(tasks) > 0
        assert all(t["status"] == "done" for t in tasks)

    def test_linhas_processadas(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        job_id = client.freeze(path, f"{tmp}/t4.permafrost", chunk_rows=3000)
        final  = client.wait(job_id, timeout=60)
        assert final.get("total_rows", 0) >= N * 0.9

    def test_chunks_sao_permafrost_valido(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, _ = csv_file
        out    = f"{tmp}/t5.permafrost"
        job_id = client.freeze(path, out, chunk_rows=5000)
        client.wait(job_id, timeout=60)
        chunks = [f for f in os.listdir(tmp) if "t5" in f and ".chunk_" in f]
        assert len(chunks) > 0
        for cf in chunks:
            assert open(os.path.join(tmp, cf), "rb").read(4) == b"PRMS"

    def test_chunks_thawable(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, _ = csv_file
        out    = f"{tmp}/t6.permafrost"
        job_id = client.freeze(path, out, chunk_rows=5000)
        client.wait(job_id, timeout=60)
        chunks = sorted([f for f in os.listdir(tmp) if "t6" in f and ".chunk_" in f])
        assert len(chunks) > 0
        df_c = pf.unfreeze(os.path.join(tmp, chunks[0]), verify=True)
        assert len(df_c) > 0


class TestConcorrencia:
    def test_multiplos_jobs_paralelos(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, _ = csv_file
        job_ids = [client.freeze(path, f"{tmp}/par_{i}.permafrost", chunk_rows=3000)
                   for i in range(3)]
        for jid in job_ids:
            final = client.wait(jid, timeout=60)
            assert final["status"] == "done", f"Job {jid}: {final['status']}"

    def test_list_jobs(self, cluster):
        client, _ = cluster
        jobs = client.list_jobs()
        assert isinstance(jobs, list) and len(jobs) > 0


class TestCancelamento:
    def test_cancel_job(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, _ = csv_file
        jid = client.freeze(path, f"{tmp}/cancel.permafrost", chunk_rows=50_000)
        time.sleep(0.1)
        r = client.cancel(jid)
        assert "cancelled" in r


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

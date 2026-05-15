"""
Testes do PermafrostCluster — Master + Worker + Client
Executar: python -m pytest tests/test_cluster.py -v
"""
import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
import numpy as np
import pandas as pd
import uvicorn
from permafrost_cluster import PermafrostMaster, PermafrostWorker, PermafrostClient
from permafrost_codec import CODEC_LZMA2, QUANT_NONE


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    """Sobe Master + 2 Workers, retorna client."""
    np.random.seed(42)
    master = PermafrostMaster(host="127.0.0.1", port=8750)
    workers = [
        PermafrostWorker("http://127.0.0.1:8750", host="127.0.0.1",
                         port=8851+i, worker_id=f"test-worker-{i+1:02d}")
        for i in range(2)
    ]
    # Subir em threads
    threading.Thread(
        target=lambda: uvicorn.run(master.app, host="127.0.0.1",
                                   port=8750, log_level="error"), daemon=True).start()
    for w in workers:
        threading.Thread(
            target=lambda ww=w: uvicorn.run(ww.app, host="127.0.0.1",
                                            port=ww.port, log_level="error"), daemon=True).start()
    time.sleep(2.5)
    for w in workers:
        try: w.register()
        except: pass
    time.sleep(0.5)

    client = PermafrostClient("http://127.0.0.1:8750")
    yield client, tmp_path_factory


@pytest.fixture(scope="module")
def csv_file(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("data")
    N = 10_000
    np.random.seed(42)
    df = pd.DataFrame({
        'id':     np.arange(1, N+1, dtype=np.int32),
        'total':  np.round(np.random.uniform(1, 5000, N), 2),
        'status': np.random.choice(['Ativo','Inativo','Pendente'], N),
        'regiao': np.random.choice(['Norte','Sul','Leste'], N),
    })
    path = str(tmp / "test_input.csv")
    df.to_csv(path, index=False)
    return path, str(tmp), N


class TestMasterHealth:
    def test_health_ok(self, cluster):
        client, _ = cluster
        h = client.health()
        assert h['status'] == 'ok'

    def test_workers_registered(self, cluster):
        client, _ = cluster
        h = client.health()
        assert h['workers'] >= 2

    def test_workers_idle(self, cluster):
        client, _ = cluster
        h = client.health()
        assert h['idle_workers'] >= 2

    def test_list_workers(self, cluster):
        client, _ = cluster
        ws = client.list_workers()
        assert len(ws) >= 2
        assert all('worker_id' in w for w in ws)
        assert all('status' in w for w in ws)


class TestJobLifecycle:
    def test_submit_job(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        out = f"{tmp}/job_basic.permafrost"
        job_id = client.freeze(path, out, chunk_rows=3000)
        assert job_id is not None and len(job_id) > 0

    def test_job_completes(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        out = f"{tmp}/job_complete.permafrost"
        job_id = client.freeze(path, out, chunk_rows=3000)
        final = client.wait(job_id, timeout=60)
        assert final['status'] == 'done'

    def test_job_all_tasks_done(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        out = f"{tmp}/job_tasks.permafrost"
        job_id = client.freeze(path, out, chunk_rows=2500)
        final = client.wait(job_id, timeout=60)
        tasks = final.get('tasks', [])
        assert len(tasks) > 0
        assert all(t['status'] == 'done' for t in tasks)

    def test_job_rows_processed(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        out = f"{tmp}/job_rows.permafrost"
        job_id = client.freeze(path, out, chunk_rows=3000)
        final = client.wait(job_id, timeout=60)
        assert final.get('total_rows', 0) >= N * 0.9

    def test_chunks_are_valid_permafrost(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        out = f"{tmp}/job_valid.permafrost"
        job_id = client.freeze(path, out, chunk_rows=5000)
        client.wait(job_id, timeout=60)
        chunk_files = [f for f in os.listdir(tmp) if 'job_valid' in f and '.chunk_' in f]
        assert len(chunk_files) > 0
        for cf in chunk_files:
            magic = open(os.path.join(tmp, cf), 'rb').read(4)
            assert magic == b'PRMS', f"{cf} não tem magic PRMS"

    def test_chunks_thawable(self, cluster, csv_file):
        from permafrost_codec import thaw
        client, _ = cluster
        path, tmp, N = csv_file
        out = f"{tmp}/job_thaw.permafrost"
        job_id = client.freeze(path, out, chunk_rows=5000)
        client.wait(job_id, timeout=60)
        chunk_files = sorted([f for f in os.listdir(tmp) if 'job_thaw' in f and '.chunk_' in f])
        assert len(chunk_files) > 0
        df_c = thaw(os.path.join(tmp, chunk_files[0]), verify=True)
        assert len(df_c) > 0


class TestConcurrency:
    def test_multiple_jobs_parallel(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        job_ids = []
        for i in range(3):
            out = f"{tmp}/parallel_{i}.permafrost"
            jid = client.freeze(path, out, chunk_rows=3000)
            job_ids.append(jid)
        for jid in job_ids:
            final = client.wait(jid, timeout=60)
            assert final['status'] == 'done', f"Job {jid}: {final['status']}"

    def test_list_jobs(self, cluster, csv_file):
        client, _ = cluster
        jobs = client.list_jobs()
        assert isinstance(jobs, list)
        assert len(jobs) > 0


class TestCancellation:
    def test_cancel_job(self, cluster, csv_file):
        client, _ = cluster
        path, tmp, N = csv_file
        out = f"{tmp}/job_cancel.permafrost"
        jid = client.freeze(path, out, chunk_rows=50_000)
        time.sleep(0.1)
        result = client.cancel(jid)
        assert 'cancelled' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])

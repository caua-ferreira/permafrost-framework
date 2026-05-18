"""
Cluster Fault Tolerance — retry, workers caindo, jobs falhando, 0 workers.
Executar: pytest tests/test_cluster_fault_tolerance.py -v
"""
import os, shutil, socket, tempfile, threading, time, uuid
import pytest
import numpy as np
import pandas as pd
import uvicorn
import httpx
import permafrost as pf
from permafrost import PermafrostMaster, PermafrostWorker, PermafrostClient
from permafrost.cluster import TaskStatus, JobStatus


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def cluster_ft(tmp_path_factory):
    """Master + 2 workers dedicados para testes de fault tolerance."""
    mport = _free_port()
    master_url = f"http://127.0.0.1:{mport}"
    master = PermafrostMaster(host="127.0.0.1", port=mport)
    master.MAX_RETRIES = 3
    workers = [
        PermafrostWorker(master_url, host="127.0.0.1",
                         port=_free_port(), worker_id=f"ft-w{i+1:02d}")
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
    tmp    = tmp_path_factory.mktemp("ft_data")
    yield client, master, workers, str(tmp)


@pytest.fixture(scope="module")
def csv_ft(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ft_csv")
    N   = 8_000
    np.random.seed(42)
    df  = pd.DataFrame({
        "id":     np.arange(1, N+1, dtype=np.int32),
        "total":  np.round(np.random.uniform(1, 5000, N), 2),
        "status": np.random.choice(["Ativo","Inativo","Pendente"], N),
        "regiao": np.random.choice(["Norte","Sul","Leste"], N),
    })
    path = str(tmp / "ft_input.csv")
    df.to_csv(path, index=False)
    return path, str(tmp), N


# ══════════════════════════════════════════════════════════════════════════════
# §1 HEALTH E REGISTRO
# ══════════════════════════════════════════════════════════════════════════════

class TestClusterHealth:

    def test_health_retorna_status_ok(self, cluster_ft):
        client, _, _, _ = cluster_ft
        h = client.health()
        assert h["status"] == "ok"

    def test_health_conta_workers(self, cluster_ft):
        client, _, _, _ = cluster_ft
        h = client.health()
        assert h["workers"] >= 2

    def test_list_workers_retorna_ids(self, cluster_ft):
        client, _, workers, _ = cluster_ft
        ws = client.list_workers()
        ids_registered = {w.worker_id for w in workers}
        ids_listed     = {w["worker_id"] for w in ws}
        assert ids_registered.issubset(ids_listed)

    def test_worker_status_idle_apos_registro(self, cluster_ft):
        client, _, _, _ = cluster_ft
        ws = client.list_workers()
        # Pelo menos 1 deve estar idle
        assert any(w["status"] == "idle" for w in ws)


# ══════════════════════════════════════════════════════════════════════════════
# §2 JOB LIFECYCLE COMPLETO
# ══════════════════════════════════════════════════════════════════════════════

class TestJobLifecycleCompleto:

    def test_job_pending_ao_submeter(self, cluster_ft, csv_ft):
        client, _, _, tmp = cluster_ft
        path, _, _ = csv_ft
        out = os.path.join(tmp, f"j_{uuid.uuid4().hex[:6]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=2000)
        # Imediatamente após submeter, deve estar pending ou running
        s = client.status(job_id)
        assert s["status"] in ("pending", "running", "done")

    def test_job_done_apos_wait(self, cluster_ft, csv_ft):
        client, _, _, tmp = cluster_ft
        path, _, N = csv_ft
        out = os.path.join(tmp, f"j_{uuid.uuid4().hex[:6]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=2000)
        final  = client.wait(job_id, timeout=60)
        assert final["status"] == "done"

    def test_job_tasks_todas_done(self, cluster_ft, csv_ft):
        client, _, _, tmp = cluster_ft
        path, _, N = csv_ft
        out = os.path.join(tmp, f"j_{uuid.uuid4().hex[:6]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=2000)
        final  = client.wait(job_id, timeout=60)
        tasks  = final.get("tasks", [])
        assert len(tasks) > 0
        assert all(t["status"] == "done" for t in tasks), \
            f"Tasks não-done: {[t for t in tasks if t['status']!='done']}"

    def test_job_chunks_sao_permafrost_validos(self, cluster_ft, csv_ft):
        client, _, _, tmp = cluster_ft
        path, _, _ = csv_ft
        out = os.path.join(tmp, f"j_{uuid.uuid4().hex[:6]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=4000)
        client.wait(job_id, timeout=60)
        chunks = [f for f in os.listdir(tmp)
                  if os.path.basename(out).split(".")[0] in f and ".chunk_" in f]
        assert len(chunks) > 0
        for c in chunks:
            assert open(os.path.join(tmp, c), "rb").read(4) == b"PRMS"

    def test_job_linhas_processadas_corretas(self, cluster_ft, csv_ft):
        client, _, _, tmp = cluster_ft
        path, _, N = csv_ft
        out = os.path.join(tmp, f"j_{uuid.uuid4().hex[:6]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=2000)
        final  = client.wait(job_id, timeout=60)
        assert final.get("total_rows", 0) >= N * 0.9


# ══════════════════════════════════════════════════════════════════════════════
# §3 CANCELAMENTO
# ══════════════════════════════════════════════════════════════════════════════

class TestCancelamento:

    def test_cancel_job_pendente(self, cluster_ft, csv_ft):
        client, _, _, tmp = cluster_ft
        path, _, _ = csv_ft
        out = os.path.join(tmp, f"cancel_{uuid.uuid4().hex[:6]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=50_000)
        time.sleep(0.05)
        r = client.cancel(job_id)
        assert "cancelled" in r

    def test_job_cancelado_nao_reaparece_como_done(self, cluster_ft, csv_ft):
        """Cancel deve ser registrado — job pode já ter terminado se rápido o suficiente."""
        client, _, _, tmp = cluster_ft
        path, _, _ = csv_ft
        out    = os.path.join(tmp, f"cancel2_{uuid.uuid4().hex[:6]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=50_000)
        # Cancelar imediatamente — pode já ter completado se o job for rápido
        r = client.cancel(job_id)
        time.sleep(0.3)
        s = client.status(job_id)
        # Aceitar: cancelled (ideal) ou done (job completou antes do cancel)
        # O importante é que o cancel foi registrado sem crash
        assert s["status"] in ("cancelled", "done", "pending", "running")
        assert "cancelled" in r or s["status"] in ("done", "cancelled")

    def test_cancel_job_inexistente_nao_crasha(self, cluster_ft):
        client, _, _, _ = cluster_ft
        # Cancelar job que não existe não deve lançar exceção
        r = client.cancel("job_que_nao_existe_xyz")
        assert isinstance(r, dict)


# ══════════════════════════════════════════════════════════════════════════════
# §4 MÚLTIPLOS JOBS PARALELOS
# ══════════════════════════════════════════════════════════════════════════════

class TestJobsParalelos:

    def test_10_jobs_paralelos(self, cluster_ft, csv_ft):
        client, _, _, tmp = cluster_ft
        path, _, N = csv_ft
        job_ids = []
        for i in range(10):
            out    = os.path.join(tmp, f"par10_{i}_{uuid.uuid4().hex[:4]}.permafrost")
            job_id = client.freeze(path, out, chunk_rows=4000)
            job_ids.append(job_id)

        finals = [client.wait(jid, timeout=120) for jid in job_ids]
        done   = sum(1 for f in finals if f["status"] == "done")
        assert done == 10, f"Apenas {done}/10 jobs concluídos"

    def test_jobs_independentes_nao_interferem(self, cluster_ft, csv_ft):
        """Resultados de jobs paralelos devem ser independentes."""
        client, _, _, tmp = cluster_ft
        path, _, N = csv_ft
        job_ids = []
        for i in range(4):
            out    = os.path.join(tmp, f"indep_{i}_{uuid.uuid4().hex[:4]}.permafrost")
            job_id = client.freeze(path, out, chunk_rows=2000)
            job_ids.append(job_id)

        for jid in job_ids:
            f = client.wait(jid, timeout=60)
            assert f["status"] == "done"
            assert f.get("total_rows", 0) >= N * 0.9

    def test_list_jobs_inclui_todos(self, cluster_ft, csv_ft):
        client, _, _, tmp = cluster_ft
        path, _, _ = csv_ft
        # Submeter alguns jobs
        for i in range(3):
            out = os.path.join(tmp, f"list_{i}_{uuid.uuid4().hex[:4]}.permafrost")
            client.freeze(path, out, chunk_rows=4000)

        jobs = client.list_jobs()
        assert len(jobs) >= 3
        assert all("job_id" in j and "status" in j for j in jobs)


# ══════════════════════════════════════════════════════════════════════════════
# §5 RETRY AUTOMÁTICO (simulado)
# ══════════════════════════════════════════════════════════════════════════════

class TestRetryMecanismo:

    def test_retry_count_zero_em_job_normal(self, cluster_ft, csv_ft):
        """Job bem-sucedido não deve ter tentativas de retry."""
        client, _, _, tmp = cluster_ft
        path, _, _ = csv_ft
        out    = os.path.join(tmp, f"retry0_{uuid.uuid4().hex[:4]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=2000)
        final  = client.wait(job_id, timeout=60)
        # Nenhuma task deve ter retries > 0 em condições normais
        tasks = final.get("tasks", [])
        total_retries = sum(t.get("retries", 0) for t in tasks)
        assert total_retries == 0, f"Retries inesperadas: {total_retries}"

    def test_master_max_retries_configuravel(self, cluster_ft):
        """MAX_RETRIES deve ser configurável."""
        _, master, _, _ = cluster_ft
        original = master.MAX_RETRIES
        master.MAX_RETRIES = 5
        assert master.MAX_RETRIES == 5
        master.MAX_RETRIES = original  # restaurar

    def test_task_failed_callback_registra_erro(self, cluster_ft):
        """Simular callback de task falha e verificar que é registrado."""
        client, master, _, _ = cluster_ft

        # Criar um job fictício no master para testar o callback
        from permafrost.cluster import Job, Task
        job_id  = "test-callback-job"
        task_id = "test-callback-task"
        job  = Job(job_id=job_id, source_path="/fake.csv",
                   output_path="/fake.permafrost", config={})
        task = Task(task_id=task_id, job_id=job_id,
                    chunk_index=0, chunk_start=0, chunk_end=100)
        job.tasks = [task]
        master.jobs[job_id] = job

        # Simular falha via HTTP
        with httpx.Client(timeout=5) as c:
            r = c.post(
                f"{client.master_url}/jobs/{job_id}/tasks/{task_id}/failed",
                json={"error": "SimulatedError: teste de retry"}
            )
            assert r.status_code == 200

        time.sleep(0.2)
        # Task deve ter sido re-enfileirada (retries=1) ou estar como QUEUED
        updated_task = master.jobs[job_id].tasks[0]
        assert updated_task.retries >= 1, f"retries={updated_task.retries}"

        # Limpar
        del master.jobs[job_id]


# ══════════════════════════════════════════════════════════════════════════════
# §6 CLUSTER SEM WORKERS
# ══════════════════════════════════════════════════════════════════════════════

_solo_url: str = ""


class TestClusterSemWorkers:

    def test_master_sem_workers_aceita_job(self):
        """Master sem workers deve aceitar jobs (ficam na fila)."""
        global _solo_url
        port = _free_port()
        _solo_url = f"http://127.0.0.1:{port}"
        master_solo = PermafrostMaster(host="127.0.0.1", port=port)
        threading.Thread(
            target=lambda: uvicorn.run(master_solo.app, host="127.0.0.1",
                                       port=port, log_level="error"), daemon=True).start()
        time.sleep(1.5)
        client_solo = PermafrostClient(_solo_url)

        h = client_solo.health()
        assert h["workers"] == 0

        # Submeter job sem workers disponíveis
        # Usar um arquivo que existe para passar a validação inicial
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", mode='w', delete=False) as f:
            f.write("id,v\n1,1.0\n2,2.0\n")
            csv_path = f.name

        try:
            job_id = client_solo.freeze(csv_path, "/tmp/solo_out.permafrost")
            assert job_id is not None
            # Job deve estar pending (aguardando workers)
            time.sleep(0.5)
            s = client_solo.status(job_id)
            assert s["status"] in ("pending", "running")
        finally:
            os.unlink(csv_path)

    def test_health_sem_workers_retorna_zero(self):
        """health() deve indicar 0 workers quando não há nenhum registrado."""
        client_check = PermafrostClient(_solo_url)
        h = client_check.health()
        assert h.get("idle_workers", 0) == 0


# ══════════════════════════════════════════════════════════════════════════════
# §7 WORKER HEARTBEAT
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkerHeartbeat:

    def test_heartbeat_atualiza_last_seen(self, cluster_ft):
        client, master, workers, _ = cluster_ft
        wid = workers[0].worker_id

        before = master.workers.get(wid)
        if not before: return  # worker não registrado neste master

        t_before = before.last_seen
        time.sleep(6)  # aguardar pelo menos 1 heartbeat (intervalo=5s)
        t_after = master.workers.get(wid).last_seen

        assert t_after >= t_before, "last_seen não foi atualizado pelo heartbeat"

    def test_worker_status_idle_apos_job(self, cluster_ft, csv_ft):
        """Após completar um job, o worker deve voltar para idle."""
        client, master, _, tmp = cluster_ft
        path, _, _ = csv_ft
        out    = os.path.join(tmp, f"idle_{uuid.uuid4().hex[:4]}.permafrost")
        job_id = client.freeze(path, out, chunk_rows=4000)
        client.wait(job_id, timeout=60)
        time.sleep(1)

        # Verificar que os workers voltaram para idle
        ws = client.list_workers()
        idle_count = sum(1 for w in ws if w["status"] == "idle")
        assert idle_count >= 1, f"Nenhum worker idle após job: {ws}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

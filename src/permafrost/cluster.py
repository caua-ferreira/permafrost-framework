"""
PermafrostCluster v1.0
Master + Worker — processamento distribuído de freeze/thaw

Arquitetura:
  PermafrostMaster  — coordena jobs, scheduling, healthcheck
  PermafrostWorker  — executa pipeline L0→L4 em chunks independentes
  PermafrostClient  — API Python para submeter jobs ao cluster

Comunicação: HTTP/JSON (FastAPI)
Estado:       dicionários em memória (evoluir para Redis em v2)
Paralelismo:  multiprocessing.Pool dentro de cada Worker

Fluxo de um JOB:
  Client → POST /jobs {source, output, config}
  Master → divide em TASKS (1 task por chunk)
  Master → distribui tasks para Workers disponíveis
  Worker → executa freeze/thaw do chunk → notifica Master
  Master → agrega resultados → marca job DONE
  Client → GET /jobs/{id} → status + métricas
"""

import sys, os
import json, time, uuid, hashlib, threading, queue
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
import pandas as pd, numpy as np

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import httpx

# ── ENUMS E MODELOS ───────────────────────────────────────────────────────────
class JobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"

class TaskStatus(str, Enum):
    QUEUED    = "queued"
    ASSIGNED  = "assigned"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    RETRY     = "retry"

@dataclass
class Task:
    task_id:     str
    job_id:      str
    chunk_index: int
    chunk_start: int
    chunk_end:   int
    status:      TaskStatus = TaskStatus.QUEUED
    worker_id:   Optional[str] = None
    result:      Optional[dict] = None
    error:       Optional[str] = None
    started_at:  Optional[float] = None
    finished_at: Optional[float] = None
    retries:     int = 0

    def to_dict(self) -> dict:
        """Serializa a task para dict JSON-serializável."""
        return asdict(self)

@dataclass
class Job:
    job_id:       str
    source_path:  str        # local path ou URI
    output_path:  str        # onde salvar o .permafrost
    config:       dict       # codec, quant, partition_by, chunk_rows
    status:       JobStatus = JobStatus.PENDING
    tasks:        List[Task] = field(default_factory=list)
    created_at:   float = field(default_factory=time.time)
    started_at:   Optional[float] = None
    finished_at:  Optional[float] = None
    total_rows:   int = 0
    stored_mb:    float = 0.0
    ratio:        float = 0.0
    error:        Optional[str] = None
    n_workers_used: int = 0

    @property
    def progress(self) -> float:
        """Fração de tasks concluídas (0.0–1.0)."""
        if not self.tasks:
            return 0.0
        done = sum(1 for t in self.tasks if t.status == TaskStatus.DONE)
        return done / len(self.tasks)

    def to_dict(self) -> dict:
        """Serializa o job para dict JSON-serializável com progresso."""
        d = asdict(self)
        d['progress'] = self.progress
        d['status'] = self.status.value
        d['tasks'] = [{**t, 'status': t['status'].value} for t in d['tasks']]
        return d

@dataclass
class WorkerInfo:
    worker_id:    str
    host:         str
    port:         int
    status:       str = "idle"     # idle | busy | offline
    current_task: Optional[str] = None
    jobs_done:    int = 0
    last_seen:    float = field(default_factory=time.time)

    @property
    def url(self) -> str:
        """URL base HTTP do worker."""
        return f"http://{self.host}:{self.port}"

    def to_dict(self) -> dict:
        """Serializa o WorkerInfo para dict."""
        return asdict(self)


# ── MASTER ────────────────────────────────────────────────────────────────────
class PermafrostMaster:
    """Coordena o cluster: recebe jobs, divide em tasks, distribui para workers.

    Scheduling: round-robin sobre workers idle.
    Fault tolerance: task falha → retry até ``MAX_RETRIES``, depois job FAILED.
    Healthcheck: workers que não respondem em ``TIMEOUT_S`` são marcados offline.

    Args:
        host: Endereço de bind do servidor FastAPI (``"0.0.0.0"`` para aceitar externo).
        port: Porta TCP do servidor (padrão 8700).
    """

    MAX_RETRIES   = 3
    TIMEOUT_S     = 30
    HEARTBEAT_S   = 10
    DEFAULT_CHUNK = 50_000

    def __init__(self, host: str = "0.0.0.0", port: int = 8700) -> None:
        self.host = host
        self.port = port
        self.jobs:    Dict[str, Job]        = {}
        self.workers: Dict[str, WorkerInfo] = {}
        self._task_queue: queue.Queue       = queue.Queue()
        self._lock = threading.RLock()
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        """Constrói e configura a aplicação FastAPI com todas as rotas REST.

        Returns:
            Instância ``FastAPI`` com rotas de health, workers, jobs e tasks.
        """
        app = FastAPI(title="PermafrostMaster", version="1.0")

        # ── Health ────────────────────────────────────────────────────────────
        @app.get("/health")
        def health() -> dict:
            with self._lock:
                return {
                    "status": "ok",
                    "jobs":    len(self.jobs),
                    "workers": len(self.workers),
                    "idle_workers": sum(1 for w in self.workers.values() if w.status == "idle"),
                }

        # ── Worker registration ───────────────────────────────────────────────
        @app.post("/workers/register")
        def register_worker(data: dict) -> dict:
            worker_id = data['worker_id']
            with self._lock:
                self.workers[worker_id] = WorkerInfo(
                    worker_id=worker_id,
                    host=data['host'],
                    port=data['port'],
                )
            print(f"  [Master] Worker registrado: {worker_id} em {data['host']}:{data['port']}")
            return {"status": "registered", "worker_id": worker_id}

        @app.post("/workers/{worker_id}/heartbeat")
        def heartbeat(worker_id: str, data: dict) -> dict:
            with self._lock:
                if worker_id in self.workers:
                    self.workers[worker_id].last_seen = time.time()
                    self.workers[worker_id].status = data.get('status', 'idle')
            return {"ok": True}

        # ── Job submission ────────────────────────────────────────────────────
        @app.post("/jobs")
        def submit_job(data: dict, background_tasks: BackgroundTasks) -> dict:
            job_id = str(uuid.uuid4())[:8]
            config = {
                'codec':        data.get('codec', 'lzma2'),
                'quant':        data.get('quant', 0),
                'partition_by': data.get('partition_by'),
                'chunk_rows':   data.get('chunk_rows', self.DEFAULT_CHUNK),
            }
            job = Job(
                job_id=job_id,
                source_path=data['source_path'],
                output_path=data.get('output_path', data['source_path'].replace('.csv', '.permafrost')),
                config=config,
            )
            with self._lock:
                self.jobs[job_id] = job
            background_tasks.add_task(self._plan_and_schedule, job_id)
            print(f"  [Master] Job submetido: {job_id} — {data['source_path']}")
            return {"job_id": job_id, "status": "pending"}

        # ── Job status ────────────────────────────────────────────────────────
        @app.get("/jobs/{job_id}")
        def get_job(job_id: str) -> dict:
            with self._lock:
                if job_id not in self.jobs:
                    raise HTTPException(404, f"Job {job_id} não encontrado")
                return self.jobs[job_id].to_dict()

        @app.get("/jobs")
        def list_jobs() -> list:
            with self._lock:
                return [j.to_dict() for j in self.jobs.values()]

        @app.delete("/jobs/{job_id}")
        def cancel_job(job_id: str) -> dict:
            with self._lock:
                if job_id in self.jobs:
                    self.jobs[job_id].status = JobStatus.CANCELLED
            return {"cancelled": job_id}

        # ── Task result (callback do worker) ──────────────────────────────────
        @app.post("/jobs/{job_id}/tasks/{task_id}/done")
        def task_done(job_id: str, task_id: str, data: dict) -> dict:
            with self._lock:
                job = self.jobs.get(job_id)
                if not job:
                    return {"ok": False}
                for task in job.tasks:
                    if task.task_id == task_id:
                        task.status      = TaskStatus.DONE
                        task.result      = data.get('result', {})
                        task.finished_at = time.time()
                        if task.worker_id in self.workers:
                            self.workers[task.worker_id].status = "idle"
                            self.workers[task.worker_id].current_task = None
                        job.total_rows += data.get('result', {}).get('rows', 0)
                        break
                self._check_job_completion(job)
            self._schedule_pending_tasks()
            return {"ok": True}

        @app.post("/jobs/{job_id}/tasks/{task_id}/failed")
        def task_failed(job_id: str, task_id: str, data: dict) -> dict:
            with self._lock:
                job = self.jobs.get(job_id)
                if not job:
                    return {"ok": False}
                for task in job.tasks:
                    if task.task_id == task_id:
                        task.error   = data.get('error', 'unknown')
                        task.retries += 1
                        if task.worker_id in self.workers:
                            self.workers[task.worker_id].status = "idle"
                            self.workers[task.worker_id].current_task = None
                        if task.retries < self.MAX_RETRIES:
                            task.status = TaskStatus.QUEUED
                            self._task_queue.put((job_id, task_id))
                            print(f"  [Master] Task {task_id} → retry {task.retries}/{self.MAX_RETRIES}")
                        else:
                            task.status = TaskStatus.FAILED
                            job.status  = JobStatus.FAILED
                            job.error   = (f"Task {task_id} falhou após "
                                           f"{self.MAX_RETRIES} tentativas: {task.error}")
                            print(f"  [Master] Job {job_id} FALHOU — {job.error}")
                        break
            return {"ok": True}

        # ── Workers status ────────────────────────────────────────────────────
        @app.get("/workers")
        def list_workers() -> list:
            with self._lock:
                return [w.to_dict() for w in self.workers.values()]

        return app

    def _plan_and_schedule(self, job_id: str) -> None:
        """Lê o arquivo-fonte, divide em tasks e enfileira para scheduling.

        Suporta ``.csv``, ``.jsonl`` / ``.ndjson`` e ``.parquet``.
        Em caso de erro, marca o job como FAILED.

        Args:
            job_id: ID do job a planejar.
        """
        time.sleep(0.1)   # dar tempo para o HTTP response chegar ao cliente

        with self._lock:
            job = self.jobs[job_id]
            job.status     = JobStatus.RUNNING
            job.started_at = time.time()

        try:
            source = job.source_path
            ext    = os.path.splitext(source)[1].lower()
            chunk  = job.config['chunk_rows']

            if ext == '.csv':
                total = sum(1 for _ in open(source)) - 1
            elif ext in ('.jsonl', '.ndjson'):
                total = sum(1 for _ in open(source) if _.strip())
            elif ext == '.parquet':
                import pyarrow.parquet as pq
                total = pq.read_metadata(source).num_rows
            else:
                total = chunk

            tasks = []
            for i, start in enumerate(range(0, total, chunk)):
                end = min(start + chunk, total)
                t = Task(
                    task_id=f"{job_id}-t{i:03d}",
                    job_id=job_id,
                    chunk_index=i,
                    chunk_start=start,
                    chunk_end=end,
                )
                tasks.append(t)
                self._task_queue.put((job_id, t.task_id))

            with self._lock:
                job.tasks = tasks
            print(f"  [Master] Job {job_id}: {len(tasks)} tasks criadas ({total:,} linhas)")

        except Exception as e:
            with self._lock:
                job.status = JobStatus.FAILED
                job.error  = str(e)
            print(f"  [Master] Erro ao planejar {job_id}: {e}")
            return

        self._schedule_pending_tasks()

    def _schedule_pending_tasks(self) -> None:
        """Distribui tasks da fila para workers idle (round-robin simples)."""
        while not self._task_queue.empty():
            with self._lock:
                idle = [w for w in self.workers.values() if w.status == "idle"]
                if not idle:
                    break
                worker = idle[0]
                try:
                    job_id, task_id = self._task_queue.get_nowait()
                except queue.Empty:
                    break
                job  = self.jobs.get(job_id)
                task = next((t for t in job.tasks if t.task_id == task_id), None) if job else None
                if not task or task.status not in (TaskStatus.QUEUED, TaskStatus.RETRY):
                    continue
                task.status     = TaskStatus.ASSIGNED
                task.worker_id  = worker.worker_id
                task.started_at = time.time()
                worker.status   = "busy"
                worker.current_task = task_id
                job.n_workers_used = max(job.n_workers_used, 1)

            self._dispatch_task(worker, job, task)

    def _dispatch_task(self, worker: WorkerInfo, job: Job, task: Task) -> None:
        """Envia uma task para um worker via HTTP POST /execute.

        Em caso de falha na comunicação, recoloca a task na fila.

        Args:
            worker: Worker de destino.
            job: Job ao qual a task pertence.
            task: Task a executar.
        """
        payload = {
            "task_id":     task.task_id,
            "job_id":      job.job_id,
            "source_path": job.source_path,
            "output_path": job.output_path,
            "chunk_start": task.chunk_start,
            "chunk_end":   task.chunk_end,
            "chunk_index": task.chunk_index,
            "config":      job.config,
            "master_url":  f"http://127.0.0.1:{self.port}",
        }
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(f"{worker.url}/execute", json=payload)
                if resp.status_code == 200:
                    with self._lock:
                        task.status = TaskStatus.RUNNING
                    print(f"  [Master] Task {task.task_id} → Worker {worker.worker_id}")
                else:
                    raise Exception(f"Worker respondeu {resp.status_code}")
        except Exception as e:
            print(f"  [Master] Falha ao enviar {task.task_id} para {worker.worker_id}: {e}")
            with self._lock:
                task.status    = TaskStatus.QUEUED
                task.worker_id = None
                if worker.worker_id in self.workers:
                    self.workers[worker.worker_id].status = "idle"
            self._task_queue.put((task.job_id, task.task_id))

    def _check_job_completion(self, job: Job) -> None:
        """Verifica se todas as tasks concluíram e marca o job como DONE ou FAILED.

        Deve ser chamado dentro do lock ``self._lock``.

        Args:
            job: Job a verificar.
        """
        if not job.tasks:
            return
        done   = [t for t in job.tasks if t.status == TaskStatus.DONE]
        failed = [t for t in job.tasks if t.status == TaskStatus.FAILED]
        if failed:
            job.status = JobStatus.FAILED
            return
        if len(done) == len(job.tasks):
            job.status      = JobStatus.DONE
            job.finished_at = time.time()
            elapsed = job.finished_at - job.started_at
            print(f"  [Master] Job {job.job_id} DONE em {elapsed:.1f}s — {job.total_rows:,} linhas")

    def run(self, **kwargs) -> None:
        """Inicia o servidor FastAPI com uvicorn (bloqueante).

        Args:
            **kwargs: Parâmetros extras repassados para ``uvicorn.run``
                (ex.: ``workers``, ``ssl_keyfile``).
        """
        uvicorn.run(self.app, host=self.host, port=self.port,
                    log_level="warning", **kwargs)


# ── WORKER ────────────────────────────────────────────────────────────────────
class PermafrostWorker:
    """Executa tasks de compressão atribuídas pelo Master.

    Cada task corresponde a 1 chunk de dados: lê as linhas designadas do
    arquivo-fonte, executa ``freeze()`` e notifica o Master com o resultado.

    Args:
        master_url: URL base do PermafrostMaster (ex.: ``"http://master:8700"``).
        host: Endereço de bind do servidor deste worker.
        port: Porta TCP do servidor (padrão 8800).
        worker_id: ID único do worker; gerado automaticamente se ``None``.
    """

    HEARTBEAT_INTERVAL = 5   # segundos

    def __init__(self, master_url: str, host: str = "127.0.0.1",
                 port: int = 8800, worker_id: str = None) -> None:
        self.master_url = master_url.rstrip('/')
        self.host       = host
        self.port       = port
        self.worker_id  = worker_id or f"worker-{str(uuid.uuid4())[:6]}"
        self.status     = "idle"
        self.app        = self._build_app()
        self._hb_thread = None

    def _build_app(self) -> FastAPI:
        """Constrói a aplicação FastAPI do worker com rotas /health e /execute.

        Returns:
            Instância ``FastAPI`` configurada.
        """
        app = FastAPI(title=f"PermafrostWorker-{self.worker_id}", version="1.0")

        @app.get("/health")
        def health() -> dict:
            return {"worker_id": self.worker_id, "status": self.status}

        @app.post("/execute")
        def execute(data: dict, background_tasks: BackgroundTasks) -> dict:
            background_tasks.add_task(self._run_task, data)
            return {"accepted": True, "task_id": data['task_id']}

        return app

    def _run_task(self, data: dict) -> None:
        """Executa o freeze de um chunk e reporta o resultado ao Master.

        Lê as linhas ``chunk_start``–``chunk_end`` do arquivo-fonte (CSV, JSONL
        ou Parquet), comprime com as configurações do job e notifica o Master
        via ``/done`` ou ``/failed``.

        Args:
            data: Payload da task recebido do Master, com campos ``task_id``,
                ``job_id``, ``source_path``, ``output_path``, ``chunk_start``,
                ``chunk_end``, ``chunk_index``, ``config`` e ``master_url``.
        """
        task_id    = data['task_id']
        job_id     = data['job_id']
        source     = data['source_path']
        output     = data['output_path']
        cfg        = data['config']
        start      = data['chunk_start']
        end        = data['chunk_end']
        chunk_idx  = data['chunk_index']
        master_url = data['master_url']
        n_rows     = end - start

        self.status = "busy"
        print(f"  [{self.worker_id}] Executando {task_id}: linhas {start:,}–{end:,}")

        try:
            ext = os.path.splitext(source)[1].lower()
            if ext == '.csv':
                df_chunk = pd.read_csv(source, skiprows=range(1, start + 1), nrows=n_rows)
            elif ext in ('.jsonl', '.ndjson'):
                import json as _json
                lines = []
                with open(source, 'r') as f:
                    for i, line in enumerate(f):
                        if i < start: continue
                        if i >= end:  break
                        if line.strip(): lines.append(_json.loads(line))
                from permafrost.schema_detector import SchemaDetector
                df_chunk, _, _ = SchemaDetector().flatten(lines)
            elif ext == '.parquet':
                import pyarrow.parquet as pq
                df_chunk = pq.read_table(source).to_pandas().iloc[start:end]
            else:
                raise ValueError(f"Formato não suportado: {ext}")

            from permafrost.codec import freeze, CODEC_LZMA2, CODEC_ZSTD, QUANT_NONE, QUANT_MEDIUM
            codec_map = {'lzma2': CODEC_LZMA2, 'zstd': CODEC_ZSTD}
            codec = codec_map.get(cfg.get('codec', 'lzma2'), CODEC_LZMA2)
            quant = cfg.get('quant', 0)

            chunk_out = f"{output}.chunk_{chunk_idx:04d}.permafrost"
            metrics = freeze(df_chunk, chunk_out, codec=codec, quant=quant,
                             partition_by=cfg.get('partition_by'))

            result = {
                'chunk_index': chunk_idx,
                'chunk_out':   chunk_out,
                'rows':        len(df_chunk),
                'stored_mb':   metrics['stored_mb'],
                'ratio':       metrics['ratio'],
            }
            self._report_done(master_url, job_id, task_id, result)
            print(f"  [{self.worker_id}] {task_id} ✓ → {metrics['stored_mb']:.3f}MB ({metrics['ratio']:.2f}×)")

        except Exception as e:
            import traceback
            error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-300:]}"
            print(f"  [{self.worker_id}] {task_id} ✗ — {error[:100]}")
            self._report_failed(master_url, job_id, task_id, error)
        finally:
            self.status = "idle"

    def _report_done(self, master_url: str, job_id: str,
                     task_id: str, result: dict) -> None:
        """Notifica o Master que a task foi concluída com sucesso.

        Args:
            master_url: URL base do Master.
            job_id: ID do job.
            task_id: ID da task.
            result: Dicionário com métricas (``rows``, ``stored_mb``, ``ratio``).
        """
        try:
            with httpx.Client(timeout=10) as c:
                c.post(f"{master_url}/jobs/{job_id}/tasks/{task_id}/done",
                       json={"result": result})
        except Exception as e:
            print(f"  [{self.worker_id}] Erro ao reportar DONE: {e}")

    def _report_failed(self, master_url: str, job_id: str,
                       task_id: str, error: str) -> None:
        """Notifica o Master que a task falhou.

        Args:
            master_url: URL base do Master.
            job_id: ID do job.
            task_id: ID da task.
            error: Mensagem de erro com traceback.
        """
        try:
            with httpx.Client(timeout=10) as c:
                c.post(f"{master_url}/jobs/{job_id}/tasks/{task_id}/failed",
                       json={"error": error})
        except Exception as e:
            print(f"  [{self.worker_id}] Erro ao reportar FAILED: {e}")

    def _start_heartbeat(self, master_url: str) -> None:
        """Inicia thread daemon de heartbeat para manter o Master informado.

        Args:
            master_url: URL base do Master para enviar os heartbeats.
        """
        def hb() -> None:
            while True:
                time.sleep(self.HEARTBEAT_INTERVAL)
                try:
                    with httpx.Client(timeout=3) as c:
                        c.post(f"{master_url}/workers/{self.worker_id}/heartbeat",
                               json={"status": self.status})
                except Exception:
                    pass
        t = threading.Thread(target=hb, daemon=True)
        t.start()

    def register(self, master_url: str = None) -> None:
        """Registra este worker no Master e inicia o heartbeat.

        Args:
            master_url: URL base do Master; usa ``self.master_url`` se ``None``.

        Raises:
            RuntimeError: Se o Master rejeitar o registro.
        """
        url = master_url or self.master_url
        with httpx.Client(timeout=5) as c:
            resp = c.post(f"{url}/workers/register", json={
                "worker_id": self.worker_id,
                "host":      self.host,
                "port":      self.port,
            })
            if resp.status_code == 200:
                print(f"  [{self.worker_id}] Registrado em {url}")
                self._start_heartbeat(url)
            else:
                raise RuntimeError(f"Falha ao registrar: {resp.text}")

    def run(self, auto_register: bool = True, **kwargs) -> None:
        """Inicia o servidor FastAPI do worker com uvicorn (bloqueante).

        Args:
            auto_register: Registra automaticamente no Master após 1.5s quando ``True``.
            **kwargs: Parâmetros extras repassados para ``uvicorn.run``.
        """
        if auto_register:
            def delayed_register() -> None:
                time.sleep(1.5)
                try:
                    self.register()
                except Exception as e:
                    print(f"Register falhou: {e}")
            threading.Thread(target=delayed_register, daemon=True).start()
        uvicorn.run(self.app, host=self.host, port=self.port,
                    log_level="warning", **kwargs)


# ── CLIENT ────────────────────────────────────────────────────────────────────
class PermafrostClient:
    """API Python de alto nível para interagir com o cluster.

    Args:
        master_url: URL base do PermafrostMaster (padrão ``"http://localhost:8700"``).

    Examples::

        client = PermafrostClient("http://localhost:8700")
        job_id = client.freeze("dados.csv", "saida.permafrost")
        status = client.wait(job_id)
    """

    def __init__(self, master_url: str = "http://localhost:8700") -> None:
        self.master_url = master_url.rstrip('/')
        self._client    = httpx.Client(timeout=30)

    def health(self) -> dict:
        """Retorna o status de saúde do Master.

        Returns:
            Dicionário com ``status``, ``jobs``, ``workers`` e ``idle_workers``.
        """
        return self._client.get(f"{self.master_url}/health").json()

    def freeze(self, source_path: str, output_path: str = None,
               codec: str = "lzma2", quant: int = 0,
               partition_by: str = None, chunk_rows: int = 50_000) -> str:
        """Submete um job de freeze distribuído ao cluster.

        Args:
            source_path: Caminho local do arquivo CSV, JSONL ou Parquet.
            output_path: Caminho de saída do ``.permafrost``; derivado de
                ``source_path`` se ``None``.
            codec: Codec de compressão (``"lzma2"`` ou ``"zstd"``).
            quant: Nível de quantização (0 = lossless).
            partition_by: Coluna para particionar o sparse index.
            chunk_rows: Linhas por chunk / task (padrão 50.000).

        Returns:
            ``job_id`` do job submetido.
        """
        payload = {
            "source_path":  source_path,
            "output_path":  output_path or source_path.replace('.csv', '.permafrost'),
            "codec":        codec,
            "quant":        quant,
            "partition_by": partition_by,
            "chunk_rows":   chunk_rows,
        }
        resp = self._client.post(f"{self.master_url}/jobs", json=payload)
        resp.raise_for_status()
        data = resp.json()
        print(f"  [Client] Job submetido: {data['job_id']}")
        return data['job_id']

    def status(self, job_id: str) -> dict:
        """Consulta o status atual de um job.

        Args:
            job_id: ID do job a consultar.

        Returns:
            Dicionário completo do job com tasks, progresso e métricas.
        """
        return self._client.get(f"{self.master_url}/jobs/{job_id}").json()

    def wait(self, job_id: str, poll_interval: float = 0.5,
             timeout: float = 300) -> dict:
        """Aguarda um job completar com polling periódico.

        Args:
            job_id: ID do job a aguardar.
            poll_interval: Intervalo entre consultas em segundos.
            timeout: Tempo máximo de espera em segundos.

        Returns:
            Dicionário de status final do job (``done``, ``failed`` ou ``cancelled``).

        Raises:
            TimeoutError: Se o job não completar dentro de ``timeout`` segundos.
        """
        start = time.time()
        while time.time() - start < timeout:
            s = self.status(job_id)
            pct  = s.get('progress', 0) * 100
            done  = sum(1 for t in s.get('tasks', []) if t['status'] == 'done')
            total = len(s.get('tasks', []))
            print(f"\r  [Client] {job_id}: {s['status']} {done}/{total} tasks ({pct:.0f}%)", end="")
            if s['status'] in ('done', 'failed', 'cancelled'):
                print()
                return s
            time.sleep(poll_interval)
        raise TimeoutError(f"Job {job_id} não completou em {timeout}s")

    def list_jobs(self) -> list:
        """Lista todos os jobs conhecidos pelo Master.

        Returns:
            Lista de dicionários de jobs.
        """
        return self._client.get(f"{self.master_url}/jobs").json()

    def list_workers(self) -> list:
        """Lista todos os workers registrados no Master.

        Returns:
            Lista de dicionários de workers com status e última atividade.
        """
        return self._client.get(f"{self.master_url}/workers").json()

    def cancel(self, job_id: str) -> dict:
        """Cancela um job em execução.

        Args:
            job_id: ID do job a cancelar.

        Returns:
            Dicionário de confirmação com ``cancelled``.
        """
        return self._client.delete(f"{self.master_url}/jobs/{job_id}").json()

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

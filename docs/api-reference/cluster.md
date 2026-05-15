# API Reference — Cluster

## PermafrostClient

```python
client = permafrost.PermafrostClient(
    master_url: str = "http://localhost:8700"
)
```

| Método | Descrição |
|--------|-----------|
| `client.health()` | Status do cluster |
| `client.freeze(source, output, codec, quant, partition_by, chunk_rows)` | Submete job, retorna `job_id` |
| `client.wait(job_id, poll_interval, timeout)` | Aguarda job, retorna status final |
| `client.status(job_id)` | Status atual de um job |
| `client.list_jobs()` | Lista todos os jobs |
| `client.list_workers()` | Lista todos os workers |
| `client.cancel(job_id)` | Cancela um job |

---

## PermafrostMaster

```python
master = permafrost.PermafrostMaster(
    host: str = "0.0.0.0",
    port: int = 8700,
)

master.MAX_RETRIES   = 3       # tentativas por task
master.HEARTBEAT_S   = 10      # timeout de heartbeat
master.DEFAULT_CHUNK = 50_000  # linhas padrão por task

master.run()  # inicia o servidor uvicorn
```

**Endpoints REST:**

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/health` | GET | Status do cluster |
| `/jobs` | POST | Submeter job |
| `/jobs` | GET | Listar jobs |
| `/jobs/{id}` | GET | Status de um job |
| `/jobs/{id}` | DELETE | Cancelar job |
| `/workers/register` | POST | Registrar worker |
| `/workers/{id}/heartbeat` | POST | Heartbeat |
| `/jobs/{job_id}/tasks/{task_id}/done` | POST | Callback de task concluída |
| `/jobs/{job_id}/tasks/{task_id}/failed` | POST | Callback de task falha |

---

## PermafrostWorker

```python
worker = permafrost.PermafrostWorker(
    master_url: str,
    host: str = "127.0.0.1",
    port: int = 8800,
    worker_id: str = None,      # auto-gerado se None
)

worker.register(master_url=None)  # registra no Master
worker.run(auto_register=True)    # inicia servidor + registro automático
```

**Endpoints REST:**

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/health` | GET | Status do worker |
| `/execute` | POST | Executar uma task (async) |

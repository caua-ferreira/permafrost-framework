# Cluster Distribuído — Master + Workers

O cluster transforma o Permafrost de biblioteca em sistema distribuído.
Um dataset de 1 TB é dividido em tasks e processado em paralelo por múltiplos workers.

---

## Arquitetura

```
PermafrostClient
    │
    │  POST /jobs  {"source": "dados.csv", "codec": "lzma2"}
    ▼
PermafrostMaster  (porta 8700)
    │  Divide em tasks (1 task por chunk de 50k linhas)
    │  Scheduling round-robin para workers idle
    │
    ├──────────────────────┬──────────────────────┐
    ▼                      ▼                      ▼
Worker-01              Worker-02              Worker-N
(porta 8801)           (porta 8802)           (porta 880N)
   │                      │                      │
freeze chunk_0         freeze chunk_1         freeze chunk_2
   │                      │                      │
POST /tasks/{id}/done  POST /tasks/{id}/done  POST /tasks/{id}/done
    └──────────────────────┴──────────────────────┘
                           │
                    Master agrega → Job DONE
```

---

## Quick Start — cluster local

### Opção 1: Python puro (desenvolvimento)

```bash
# Terminal 1: Master
python -m permafrost.cluster master --port=8700

# Terminal 2 e 3: Workers
python -m permafrost.cluster worker --master=http://localhost:8700 --port=8801
python -m permafrost.cluster worker --master=http://localhost:8700 --port=8802
```

### Opção 2: Docker Compose (recomendado)

```bash
# No diretório do projeto
docker-compose up --scale worker=4
# → 1 master + 4 workers prontos em < 30 segundos
```

---

## Submeter jobs via Python

```python
import permafrost as pf

client = pf.PermafrostClient("http://localhost:8700")

# Verificar saúde do cluster
health = client.health()
print(f"Workers: {health['workers']} | Idle: {health['idle_workers']}")

# Submeter job de freeze
job_id = client.freeze(
    "dados.csv",
    "dados.permafrost",
    codec="lzma2",
    chunk_rows=50_000,
)
print(f"Job submetido: {job_id}")

# Aguardar com progress
status = client.wait(job_id, poll_interval=0.5)
print(f"Status: {status['status']}")
print(f"Linhas processadas: {status['total_rows']:,}")
print(f"Tasks: {len(status['tasks'])}")
```

---

## Monitoramento

```python
# Listar todos os jobs
jobs = client.list_jobs()
for job in jobs:
    print(f"{job['job_id']}: {job['status']} ({job['progress']*100:.0f}%)")

# Listar workers
workers = client.list_workers()
for w in workers:
    print(f"{w['worker_id']}: {w['status']} | jobs_done={w['jobs_done']}")

# Cancelar job
client.cancel(job_id)
```

---

## Múltiplos jobs paralelos

O Master suporta múltiplos jobs simultâneos. Workers idle são automaticamente
alocados para tasks de qualquer job na fila.

```python
import permafrost as pf

client = pf.PermafrostClient("http://localhost:8700")

# Submeter 3 jobs ao mesmo tempo
job_ids = [
    client.freeze("vendas_2022.csv", "vendas_2022.permafrost"),
    client.freeze("vendas_2023.csv", "vendas_2023.permafrost"),
    client.freeze("clientes.csv",    "clientes.permafrost"),
]

# Aguardar todos
results = [client.wait(jid) for jid in job_ids]
for r in results:
    print(f"{r['status']} — {r['total_rows']:,} linhas")
```

---

## Fault tolerance

O Master implementa retry automático até 3 vezes por task:

```
Task falha no Worker-01
    → Master re-enfileira a task
    → Atribui ao Worker-02 (ou Worker-01 quando idle)
    → Se falhar 3 vezes → Job marcado como FAILED
```

Heartbeat: workers enviam ping a cada 5s. Workers que pararem de responder
são marcados como `offline` e suas tasks são redistribuídas.

---

## Configuração avançada

```python
import permafrost as pf

# Master com configurações customizadas
master = pf.PermafrostMaster(
    host="0.0.0.0",
    port=8700,
)
master.MAX_RETRIES   = 5      # tentativas por task
master.HEARTBEAT_S   = 10     # intervalo de heartbeat
master.DEFAULT_CHUNK = 50_000 # linhas padrão por task

# Worker em host remoto
worker = pf.PermafrostWorker(
    master_url="http://master-host:8700",
    host="0.0.0.0",
    port=8801,
    worker_id="worker-prod-01",
)
worker.register()
```

"""
Exemplo 05 — Processamento Distribuído (Cluster)
Demonstra PermafrostMaster + PermafrostWorker + PermafrostClient.
Executar: python examples/05_cluster_distributed.py
"""

# ──────────────────────────────────────────────────────────────────────
# ❄️ Permafrost — Processamento Distribuído (Cluster)
# ──────────────────────────────────────────────────────────────────────
# Este notebook demonstra o **modo cluster** do Permafrost:
# um Master coordena N Workers que comprimem chunks em paralelo.
# **Cenário:** Dataset de 500k linhas que precisa ser comprimido
# distribuindo a carga entre múltiplos processos/máquinas.
# O que vamos fazer:
# 1. Subir um Master e 2 Workers em threads locais
# 2. Submeter um job de freeze via `PermafrostClient`
# 3. Monitorar o progresso em tempo real
# 4. Verificar os chunks produzidos
# 5. Escalar para múltiplos workers
# > **Nota:** Para produção, Master e Workers rodam em processos/containers separados.
# > Aqui usamos threads para simular tudo localmente.

import permafrost as pf
import pandas as pd
import numpy as np
import tempfile, os, time, threading

print(f"Permafrost {pf.__version__}")

WORKDIR = tempfile.mkdtemp(prefix='pf_cluster_')
print(f"Diretório de trabalho: {WORKDIR}")

# ── 1. Gerando o dataset ───────────────────────────
# Simularemos logs de servidor: timestamps, IPs, endpoints, status codes, latências.

np.random.seed(42)
N = 200_000

ENDPOINTS = [
    '/api/v1/users', '/api/v1/products', '/api/v1/orders',
    '/api/v2/search', '/api/v2/recommend', '/health', '/metrics',
    '/api/v1/auth/login', '/api/v1/auth/logout', '/api/v1/cart',
]
METHODS  = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']
STATUSES = [200, 200, 200, 200, 201, 204, 400, 401, 403, 404, 500]

df = pd.DataFrame({
    'timestamp':  pd.date_range('2024-01-01', periods=N, freq='500ms'),
    'server_id':  np.random.randint(1, 20, N),
    'endpoint':   np.random.choice(ENDPOINTS, N),
    'method':     np.random.choice(METHODS, N, p=[0.6, 0.2, 0.1, 0.05, 0.05]),
    'status':     np.random.choice(STATUSES, N),
    'latency_ms': np.round(np.abs(np.random.lognormal(3.5, 0.8, N)), 1),
    'bytes_sent': np.random.randint(100, 50_000, N),
    'user_agent': np.random.choice(['Mozilla/5.0', 'curl/7.68', 'Python/httpx', 'Go-http'], N),
    'region':     np.random.choice(['us-east', 'us-west', 'eu-west', 'ap-south'], N),
})

# Salvar como CSV para o cluster ler
csv_path = os.path.join(WORKDIR, 'server_logs.csv')
df.to_csv(csv_path, index=False)

csv_mb = os.path.getsize(csv_path) / 1e6
print(f"Dataset: {len(df):,} linhas × {len(df.columns)} colunas")
print(f"CSV:     {csv_mb:.2f} MB")
df.head(3)

# ── 2. Subindo o cluster localmente ────────────────
# O `PermafrostMaster` coordena jobs. Os `PermafrostWorkers` processam chunks.
# Aqui usamos `threading` para simular múltiplos processos na mesma máquina.
# Em produção, cada Worker rodaria em um container Docker separado.

import asyncio, sys

# Windows: uvicorn in threads needs SelectorEventLoop
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from permafrost import PermafrostMaster, PermafrostWorker, PermafrostClient

MASTER_PORT = 18700
N_WORKERS   = 2

# ── Subir Master em background ────────────────────────────────────────────
master = PermafrostMaster(host='127.0.0.1', port=MASTER_PORT)
t_master = threading.Thread(
    target=master.run,
    kwargs={'log_level': 'error'},
    daemon=True,
)
t_master.start()
time.sleep(3.0)  # aguarda o servidor iniciar
print(f"Master rodando em http://127.0.0.1:{MASTER_PORT}")

# ── Subir Workers em background ───────────────────────────────────────────
workers = []
for i in range(N_WORKERS):
    port = MASTER_PORT + 100 + i
    w = PermafrostWorker(
        master_url=f'http://127.0.0.1:{MASTER_PORT}',
        host='127.0.0.1',
        port=port,
        worker_id=f'worker-{i+1:02d}',
    )
    t = threading.Thread(
        target=w.run,
        kwargs={'auto_register': True, 'log_level': 'error'},
        daemon=True,
    )
    t.start()
    workers.append(w)
    print(f"  Worker {i+1} na porta {port}")

time.sleep(5.0)  # aguarda registro dos workers
print(f"\nCluster pronto com {N_WORKERS} workers!")

# ── 3. Verificando saúde do cluster ────────────────

client = PermafrostClient(f'http://127.0.0.1:{MASTER_PORT}')

health = client.health()
print("Status do cluster:")
print(f"  Status:        {health['status']}")
print(f"  Workers:       {health['workers']}")
print(f"  Workers idle:  {health['idle_workers']}")
print(f"  Jobs:          {health['jobs']}")

workers_info = client.list_workers()
print(f"\nWorkers registrados:")
for w in workers_info:
    print(f"  {w['worker_id']:15s} {w['host']}:{w['port']}  status={w['status']}")

# ── 4. Submetendo um job de freeze ─────────────────
# O Master vai dividir os 200k linhas em chunks de 50k e distribuir entre os Workers.

output_base = os.path.join(WORKDIR, 'server_logs_cluster.permafrost')

t0 = time.time()
job_id = client.freeze(
    source_path=csv_path,
    output_path=output_base,
    codec='zstd',          # Zstd para máxima velocidade
    chunk_rows=50_000,
    partition_by='region',
)

print(f"Job submetido: {job_id}")
print(f"Aguardando conclusão...\n")

# Polling com progresso
final_status = client.wait(job_id, poll_interval=0.3, timeout=120)

elapsed = time.time() - t0
print(f"\nJob {job_id} concluído em {elapsed:.1f}s")
print(f"Status:      {final_status['status']}")
print(f"Total linhas: {final_status['total_rows']:,}")

# ── 5. Inspecionando os chunks produzidos ──────────

# Listar os arquivos .permafrost produzidos pelos workers
chunks = sorted([
    f for f in os.listdir(WORKDIR)
    if f.endswith('.permafrost')
])

total_pf_mb = 0
print(f"Chunks produzidos: {len(chunks)}")
print(f"{'Arquivo':50s} {'Tamanho':>10}  {'Linhas':>8}  {'Ratio':>8}")
print('─' * 80)

for fname in chunks:
    fpath = os.path.join(WORKDIR, fname)
    size_mb = os.path.getsize(fpath) / 1e6
    total_pf_mb += size_mb
    try:
        info = pf.audit(fpath)
        rows  = info['orig_rows']
        ratio = info.get('ratio', '?')
    except Exception:
        rows, ratio = '?', '?'
    print(f"  {fname:48s} {size_mb:8.3f}MB  {str(rows):>8}  {str(ratio):>8}")

print('─' * 80)
print(f"\n  CSV original:   {csv_mb:.2f} MB")
print(f"  Total chunks:   {total_pf_mb:.3f} MB")
if total_pf_mb > 0:
    print(f"  Ratio total:    {csv_mb / total_pf_mb:.2f}×")

# ── 6. Detalhes do job — tasks e workers ───────────

job_details = client.status(job_id)

print(f"Job {job_id}:")
print(f"  Status:          {job_details['status']}")
print(f"  Total linhas:    {job_details['total_rows']:,}")
print(f"  Progresso:       {job_details['progress']*100:.0f}%")
print(f"  Workers usados:  {job_details['n_workers_used']}")

print(f"\nTasks ({len(job_details['tasks'])}):") 
tasks_df = pd.DataFrame([
    {
        'task_id':    t['task_id'],
        'status':     t['status'],
        'worker_id':  t.get('worker_id', '—'),
        'linhas':     (t['chunk_end'] - t['chunk_start']),
        'tempo_s':    round((t.get('finished_at') or 0) - (t.get('started_at') or 0), 2)
                      if t.get('finished_at') and t.get('started_at') else '—',
    }
    for t in job_details['tasks']
])
print(tasks_df.to_string(index=False))

# ── 7. Múltiplos jobs em paralelo ──────────────────
# O cluster suporta múltiplos jobs simultâneos — cada Worker pega tarefas de qualquer job.

# Criar 3 CSVs menores para submeter simultaneamente
job_ids = []
for i in range(3):
    df_i = df.sample(20_000, random_state=i).reset_index(drop=True)
    csv_i = os.path.join(WORKDIR, f'batch_{i}.csv')
    df_i.to_csv(csv_i, index=False)
    out_i = os.path.join(WORKDIR, f'batch_{i}.permafrost')
    jid = client.freeze(source_path=csv_i, output_path=out_i, codec='zstd', chunk_rows=10_000)
    job_ids.append(jid)
    print(f"  Job {i+1} submetido: {jid}")

print(f"\n{len(job_ids)} jobs em execução simultânea...")

# Aguardar todos
t0 = time.time()
for jid in job_ids:
    s = client.wait(jid, poll_interval=0.2, timeout=60)
    print(f"  {jid}: {s['status']} ({s['total_rows']:,} linhas)")

print(f"\nTodos concluídos em {time.time()-t0:.1f}s")

# ── 8. Cancelamento de job ─────────────────────────

# Submeter um job e cancelar imediatamente
csv_cancel = os.path.join(WORKDIR, 'cancel_test.csv')
df.head(5_000).to_csv(csv_cancel, index=False)

jid_cancel = client.freeze(
    source_path=csv_cancel,
    output_path=os.path.join(WORKDIR, 'cancel_test.permafrost'),
    codec='lzma2',
)

# Cancelar antes de completar
result = client.cancel(jid_cancel)
time.sleep(0.5)

status = client.status(jid_cancel)
print(f"Job {jid_cancel}:")
print(f"  Status após cancelar: {status['status']}")

# ── 9. Docker Compose para produção ────────────────
# Para rodar em produção com containers Docker, use o `docker-compose.yml` do repositório:
# ```yaml
# ──────────────────────────────────────────────────────────────────────
# docker-compose.yml
# ──────────────────────────────────────────────────────────────────────
# version: '3.8'
# services:
#   master:
#     image: caua-ferreira/permafrost-master:latest
#     ports:
#       - "8700:8700"
#     environment:
#       - PERMAFROST_HOST=0.0.0.0
#       - PERMAFROST_PORT=8700
#   worker-1:
#     image: caua-ferreira/permafrost-worker:latest
#     environment:
#       - MASTER_URL=http://master:8700
#       - WORKER_PORT=8800
#     volumes:
#       - ./data:/data
#     depends_on: [master]
#   worker-2:
#     image: caua-ferreira/permafrost-worker:latest
#     environment:
#       - MASTER_URL=http://master:8700
#       - WORKER_PORT=8801
#     volumes:
#       - ./data:/data
#     depends_on: [master]
# ```
# ```bash
# ──────────────────────────────────────────────────────────────────────
# Escalar para 8 workers:
# ──────────────────────────────────────────────────────────────────────
# docker-compose up --scale worker=8
# ```
# O cliente Python funciona identicamente:
# ```python
# client = pf.PermafrostClient('http://localhost:8700')
# job_id = client.freeze('dados.csv', 's3://bucket/dados.permafrost')
# status = client.wait(job_id)
# ```

# ── 10. Arquitetura resumida ───────────────────────
# ```
# PermafrostClient
#        │
#        │  POST /jobs
#        ▼
# PermafrostMaster  ──────── divide CSV em tasks (1 task = 1 chunk)
#        │                         │
#        │  POST /execute          ▼
#        ├──────────► Worker-1 → freeze(chunk_0) → chunk_0.permafrost
#        ├──────────► Worker-2 → freeze(chunk_1) → chunk_1.permafrost
#        └──────────► Worker-N → freeze(chunk_N) → chunk_N.permafrost
#        │
#        │  POST /done (callback)
#        │
#        └──► agrega resultados → job DONE
# ```
# | Componente | Responsabilidade |
# |-----------|------------------|
# | `PermafrostMaster` | Scheduling, fault tolerance (retry 3×), healthcheck |
# | `PermafrostWorker` | Executa freeze/thaw de 1 chunk, heartbeat |
# | `PermafrostClient` | API Python de alto nível (`freeze`, `wait`, `status`) |
# Próximos passos:
# - Adicionar Redis para persistência de estado entre reinicializações
# - Implementar merge automático dos chunks em um único `.permafrost`
# - Métricas Prometheus no endpoint `/metrics` do Master

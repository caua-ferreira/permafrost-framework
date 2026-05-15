"""
Exemplo 04 — Cluster com Docker
Demonstra como usar o PermafrostClient para processar dados
em um cluster rodando via Docker Compose.

Pré-requisitos:
  1. Docker e Docker Compose instalados
  2. Imagens disponíveis (Docker Hub ou build local)

Iniciar o cluster:
  # Opção A: Docker Hub (imagens publicadas)
  docker-compose up --scale worker=4 -d

  # Opção B: Build local
  docker-compose -f docker-compose.yml -f docker-compose.dev.yml up --scale worker=4 -d

Executar este exemplo:
  python examples/04_cluster_docker.py

Encerrar o cluster:
  docker-compose down
"""
import permafrost as pf
import pandas as pd
import numpy as np
import time
import sys
import os

MASTER_URL = os.environ.get("PERMAFROST_MASTER_URL", "http://localhost:8700")
DATA_DIR   = os.environ.get("PERMAFROST_DATA_DIR", "/tmp/cluster_demo")

os.makedirs(DATA_DIR, exist_ok=True)

print("❄  Permafrost — Cluster com Docker\n")
print(f"Master: {MASTER_URL}")
print(f"Dados:  {DATA_DIR}\n")


# ── 1. Verificar cluster ──────────────────────────────────────────────────────
print("[1] Verificando cluster...")
client = pf.PermafrostClient(MASTER_URL)

try:
    health = client.health()
except Exception as e:
    print(f"  ✗ Cluster não acessível: {e}")
    print("  Inicie com: docker-compose up --scale worker=4 -d")
    sys.exit(1)

print(f"  ✓ Master OK")
print(f"  Workers registrados: {health['workers']}")
print(f"  Workers disponíveis: {health['idle_workers']}")

if health['workers'] == 0:
    print("  ✗ Nenhum worker disponível — aguarde alguns segundos e tente novamente")
    sys.exit(1)


# ── 2. Gerar dados de teste ───────────────────────────────────────────────────
print("\n[2] Gerando datasets de teste...")
np.random.seed(42)

datasets = {}
for ano in [2022, 2023, 2024]:
    N = 30_000
    df = pd.DataFrame({
        "id":     np.arange(1, N+1, dtype="int32"),
        "ano":    ano,
        "regiao": np.random.choice(["Norte","Sul","Leste","Oeste"], N),
        "total":  np.round(np.random.uniform(1, 50000, N), 2),
        "status": np.random.choice(["Ativo","Cancelado","Pendente"], N),
        "canal":  np.random.choice(["Online","Loja","App"], N),
    })
    path_csv = os.path.join(DATA_DIR, f"vendas_{ano}.csv")
    df.to_csv(path_csv, index=False)
    datasets[ano] = path_csv
    print(f"  vendas_{ano}.csv: {N:,} linhas ({os.path.getsize(path_csv)/1e6:.2f} MB)")


# ── 3. Submeter jobs paralelos ────────────────────────────────────────────────
print(f"\n[3] Submetendo {len(datasets)} jobs em paralelo...")
job_ids = {}
t0_total = time.time()

for ano, csv_path in datasets.items():
    out_path = os.path.join(DATA_DIR, f"vendas_{ano}.permafrost")
    job_id = client.freeze(
        csv_path,
        out_path,
        codec="lzma2",
        chunk_rows=10_000,
    )
    job_ids[ano] = job_id
    print(f"  [{ano}] Job submetido: {job_id}")


# ── 4. Monitorar progresso ────────────────────────────────────────────────────
print(f"\n[4] Aguardando conclusão (timeout=120s)...")
results = {}

for ano, job_id in job_ids.items():
    final = client.wait(job_id, poll_interval=0.3, timeout=120)
    results[ano] = final
    status = final["status"]
    rows   = final.get("total_rows", 0)
    tasks  = len(final.get("tasks", []))
    icon   = "✓" if status == "done" else "✗"
    print(f"  {icon} [{ano}] {status} — {rows:,} linhas em {tasks} tasks")

t_total = time.time() - t0_total


# ── 5. Verificar arquivos gerados ─────────────────────────────────────────────
print(f"\n[5] Verificando arquivos .permafrost...")
total_original_mb = 0
total_stored_mb   = 0

for ano in datasets:
    csv_path = datasets[ano]
    out_path = os.path.join(DATA_DIR, f"vendas_{ano}.permafrost")

    # Coletar chunks gerados
    chunks = sorted([
        f for f in os.listdir(DATA_DIR)
        if f.startswith(f"vendas_{ano}") and ".chunk_" in f
    ])

    if not chunks:
        print(f"  ✗ [{ano}] Nenhum chunk encontrado")
        continue

    # Verificar cada chunk
    chunk_ok = all(
        open(os.path.join(DATA_DIR, c), "rb").read(4) == b"PRMS"
        for c in chunks
    )

    # Audit de um chunk
    info = pf.audit(os.path.join(DATA_DIR, chunks[0]))
    csv_mb  = os.path.getsize(csv_path) / 1e6
    pf_mb   = sum(os.path.getsize(os.path.join(DATA_DIR, c)) / 1e6 for c in chunks)

    total_original_mb += csv_mb
    total_stored_mb   += pf_mb

    ratio = csv_mb / pf_mb if pf_mb > 0 else 0
    print(f"  ✓ [{ano}] {len(chunks)} chunks | {csv_mb:.2f}MB → {pf_mb:.3f}MB | ratio={ratio:.2f}× | magic={'✓' if chunk_ok else '✗'}")


# ── 6. Resumo final ───────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print(f"RESUMO DO CLUSTER")
print(f"{'='*55}")
print(f"  Workers utilizados:  {health['workers']}")
print(f"  Jobs processados:    {len(job_ids)}")
print(f"  Tempo total:         {t_total:.1f}s")
print(f"  Original total:      {total_original_mb:.2f} MB")
print(f"  Armazenado total:    {total_stored_mb:.3f} MB")
if total_stored_mb > 0:
    print(f"  Ratio global:        {total_original_mb/total_stored_mb:.2f}×")

all_done = all(r["status"] == "done" for r in results.values())
print(f"\n  {'✓ Todos os jobs concluídos!' if all_done else '✗ Alguns jobs falharam'}")
print(f"\n  Para encerrar o cluster:")
print(f"  docker-compose down")

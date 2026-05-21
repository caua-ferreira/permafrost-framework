"""
Exemplo 03 — MongoDB Dump (NoSQL)
Demonstra como arquivar dumps do MongoDB com campos aninhados.
Executar: python examples/03_mongodb_dump.py
"""

# ──────────────────────────────────────────────────────────────────────
# ❄️ Permafrost — MongoDB Dump (NoSQL)
# ──────────────────────────────────────────────────────────────────────
# Este notebook mostra como arquivar dumps do MongoDB (ou qualquer banco
# NoSQL) com o Permafrost.
# **Cenário real:** Backup mensal de uma coleção MongoDB de e-commerce
# com documentos aninhados: pedidos, itens, endereços, histórico de status.
# O que vamos fazer:
# 1. Gerar documentos MongoDB-style com campos aninhados
# 2. Exportar para JSONL (equivale ao `mongoexport`)
# 3. Usar `SchemaDetector` para flatten automático
# 4. Comprimir e comparar com `mongodump` (gzip)
# 5. Demonstrar busca no catalog sem descomprimir

import permafrost as pf
import pandas as pd
import numpy as np
import json, random, os, gzip, tempfile
from datetime import datetime, timedelta

print(f"Permafrost {pf.__version__}")

import tempfile, os
WORKDIR = tempfile.mkdtemp(prefix='pf_demo_')
print(f'Arquivos temporários em: {WORKDIR}')

# ── 1. Gerando documentos MongoDB-style ────────────
# Documentos de pedidos com campos aninhados típicos do MongoDB.

random.seed(42)
np.random.seed(42)

PRODUTOS = [
    {'id': f'PROD-{i:04d}', 'nome': f'Produto {i}',
     'categoria': random.choice(['Eletronicos','Vestuario','Casa','Esportes','Livros']),
     'preco': round(random.uniform(10, 2000), 2)}
    for i in range(1, 201)
]

ESTADOS = ['SP','RJ','MG','RS','PR','SC','BA','CE','GO','DF']
STATUS_FLOW = ['pending', 'confirmed', 'shipped', 'delivered']

def generate_order(i: int) -> dict:
    created = datetime(2022, 1, 1) + timedelta(hours=i * 3)
    n_items = random.randint(1, 5)
    items = []
    total = 0.0
    for _ in range(n_items):
        p = random.choice(PRODUTOS)
        qty = random.randint(1, 3)
        items.append({
            'produto_id': p['id'],
            'nome':       p['nome'],
            'quantidade': qty,
            'preco_unit': p['preco'],
            'subtotal':   round(p['preco'] * qty, 2),
        })
        total += p['preco'] * qty

    n_status = random.randint(1, len(STATUS_FLOW))
    history = []
    for j in range(n_status):
        history.append({
            'status': STATUS_FLOW[j],
            'ts': (created + timedelta(hours=j * 12)).isoformat(),
        })

    return {
        '_id':          f'order_{i:08d}',
        'cliente_id':   f'cli_{random.randint(1, 20000):06d}',
        'created_at':   created.isoformat(),
        'status':       STATUS_FLOW[n_status - 1],
        'total':        round(total, 2),
        'n_items':      n_items,
        'itens':        items,
        'endereco': {
            'estado':   random.choice(ESTADOS),
            'cidade':   f'Cidade {random.randint(1, 100)}',
            'cep':      f'{random.randint(10000, 99999):05d}-{random.randint(100, 999)}',
        },
        'historico':    history,
        'frete':        round(random.uniform(5, 80), 2),
        'cupom':        f'DESC{random.randint(5,30)}' if random.random() < 0.3 else None,
        'avaliacao':    random.randint(1, 5) if STATUS_FLOW[n_status-1] == 'delivered' else None,
    }

N_ORDERS = 30_000
jsonl_path = os.path.join(WORKDIR, 'mongodb_orders.jsonl')

print(f"Gerando {N_ORDERS:,} documentos...")
with open(jsonl_path, 'w', encoding='utf-8') as f:
    for i in range(N_ORDERS):
        f.write(json.dumps(generate_order(i), ensure_ascii=False) + '\n')

jsonl_mb = os.path.getsize(jsonl_path) / 1e6
print(f"Export JSONL: {jsonl_mb:.2f} MB")

# Preview do primeiro documento
with open(jsonl_path) as f:
    doc = json.loads(f.readline())
print(f"\nExemplo de documento:")
print(json.dumps(doc, indent=2, ensure_ascii=False)[:600] + '...')

# ── 2. Schema detection e flatten automático ───────
# O SchemaDetector expande campos aninhados automaticamente
# (ex.: `endereco.estado`, `historico[0].status`).

det = pf.SchemaDetector(sample_size=1000)

with open(jsonl_path) as f:
    sample_docs = [json.loads(l) for l in f if l.strip()][:1000]

schema_df, schema_info, stats = det.flatten(sample_docs)

print(f"Campos originais nos documentos: ~15")
print(f"Colunas após flatten:             {len(schema_df.columns)}")
print(f"\nColunas geradas:")
for col in schema_df.columns:
    dtype = str(schema_df[col].dtype)
    null_pct = schema_df[col].isna().mean() * 100
    print(f"  {col:35s} {dtype:15s} {null_pct:.0f}% nulos")

# ── 3. Comparativo: mongodump BSON.gz vs Permafrost ─

# Simular mongodump (JSONL + gzip, que é o que a maioria usa)
gz_path = os.path.join(WORKDIR, 'mongodb_orders.jsonl.gz')
with open(jsonl_path, 'rb') as f_in:
    with gzip.open(gz_path, 'wb') as f_out:
        f_out.write(f_in.read())

gz_mb = os.path.getsize(gz_path) / 1e6
print(f"JSONL + gzip (mongodump-like): {gz_mb:.2f} MB")

pf_path = os.path.join(WORKDIR, 'mongodb_orders.permafrost')

def progress(rows, chunks, mb):
    print(f"\r  {rows:,} docs | {chunks} chunks | {mb:.2f} MB", end='')

print("Comprimindo com Permafrost...")
metrics = pf.freeze_file(
    jsonl_path, pf_path,
    codec=pf.CODEC_LZMA2,
    chunk_rows=5_000,
    progress_cb=progress,
)
print()

pf_mb = os.path.getsize(pf_path) / 1e6

print(f"\n{'─'*50}")
print(f"{'Formato':<30} {'Tamanho':>10} {'vs JSONL':>12}")
print(f"{'─'*50}")
print(f"{'JSONL bruto (mongoexport)':<30} {jsonl_mb:>8.2f} MB {'1.00×':>12}")
print(f"{'JSONL + gzip (mongodump)':<30} {gz_mb:>8.2f} MB {jsonl_mb/gz_mb:>11.2f}×")
print(f"{'Permafrost LZMA2':<30} {pf_mb:>8.2f} MB {metrics['ratio']:>11.1f}×")
print(f"{'─'*50}")
print(f"\nPermafrost é {gz_mb/pf_mb:.1f}× menor que gzip")

# ── 4. Usando o Catalog para gerenciar backups ─────
# O `PermafrostCatalog` é um índice DuckDB que permite buscar e consultar
# metadados de milhares de arquivos sem baixar nenhum deles.

cat = pf.PermafrostCatalog(os.path.join(WORKDIR, 'catalog.db'))

# Registrar o arquivo com tags de negócio
cat.register(
    pf_path,
    tags=['mongodb', 'orders', 'e-commerce', 'prod']
)

print("Arquivo registrado no catalog.")

# Buscar no catalog
results = cat.search(tags_contain='mongodb')
print(f"\nResultados para tag 'mongodb': {len(results)} arquivo(s)")
if not results.empty:
    r = results.iloc[0]
    print(f"  Nome:       {r['name']}")
    print(f"  Linhas:     {r['rows']:,}")
    print(f"  Tamanho:    {r['mb']:.2f} MB")
    print(f"  Codec:      {r['codec']}")
    print(f"  Chunks:     {r['n_chunks']}")

# ── 5. Verificação de integridade em lote ──────────

report = cat.integrity_check()

print(f"Verificação de integridade:")
print(f"  Arquivos verificados: {report.get('checked', 0)}")
print(f"  OK:                   {report.get('ok', 0)}")
print(f"  Corrompidos:          {report.get('corrupt', 0)}")
print(f"  Ausentes:             {report.get('missing', 0)}")

# ── Resumo ─────────────────────────────────────────
# O Permafrost é ideal para backups MongoDB porque:
# | Problema do MongoDB dump | Solução Permafrost |
# |--------------------------|-------------------|
# | BSON + gzip = 5–8× ratio | Preditores + LZMA2 = 15–30× |
# | Precisa descomprimir tudo para buscar | Sparse index → lê só o que precisa |
# | Sem verificação de integridade | SHA-256 por chunk |
# | Sem catalog de metadados | DuckDB embutido |
# **Próximo:** `04_s3_glacier_archive.ipynb` — upload automático para cloud.

"""
Exemplo 02 — Dados de Redes Sociais (JSONL)
Demonstra SchemaDetector, freeze_file() e comparativo de compressão com JSONL.
Executar: python examples/02_social_media_jsonl.py
"""

# ──────────────────────────────────────────────────────────────────────
# ❄️ Permafrost — Dados de Redes Sociais (JSONL)
# ──────────────────────────────────────────────────────────────────────
# Este notebook demonstra como usar o Permafrost com dados JSONL —
# o formato mais comum em dumps de Twitter, Instagram, Reddit e MongoDB.
# **Cenário:** Você tem um arquivo de posts de rede social em JSONL.
# Quer arquivar tudo no S3 Glacier pagando o mínimo possível.
# O que vamos fazer:
# 1. Gerar dataset JSONL sintético (estilo Twitter)
# 2. Detectar schema automaticamente com `SchemaDetector`
# 3. Comprimir com `freeze_file()` (sem carregar tudo na RAM)
# 4. Ver o ratio de compressão absurdo do JSONL
# 5. Calcular economia de custo no Glacier

import permafrost as pf
import pandas as pd
import numpy as np
import json, random, os, time
from datetime import datetime, timedelta

print(f"Permafrost {pf.__version__}")

import tempfile, os
WORKDIR = tempfile.mkdtemp(prefix='pf_demo_')
print(f'Arquivos temporários em: {WORKDIR}')

# ── 1. Gerando dataset JSONL sintético ─────────────
# Simularemos posts de rede social com campos aninhados típicos.

random.seed(42)
np.random.seed(42)

USERS = [f"@user_{i}" for i in range(1, 5001)]
HASHTAGS = ['#tech', '#python', '#data', '#ai', '#ml', '#cloud', '#opensource',
            '#startup', '#dev', '#coding', '#analytics', '#bigdata']
PLATFORMS = ['twitter', 'instagram', 'reddit', 'linkedin']
SENTIMENTS = ['positive', 'negative', 'neutral']
LANGUAGES = ['pt', 'en', 'es', 'fr', 'de']

SAMPLE_TEXTS = [
    "Incrível como o machine learning está transformando a industria",
    "Acabei de descobrir o Permafrost Framework, ratio de 10x no meu dataset",
    "Python é definitivamente a melhor linguagem para análise de dados",
    "O custo de storage no S3 Glacier é absurdamente baixo comparado ao Standard",
    "Mais um dia de debugging, mais um dia de aprendizado",
    "Open source é o futuro da tecnologia",
    "Compressão inteligente pode reduzir seus custos em até 88%",
    "DataFrame operations no pandas ainda são as melhores",
]

def generate_post(i: int) -> dict:
    ts = datetime(2023, 1, 1) + timedelta(minutes=i * 2)
    return {
        'id':          f"post_{i:08d}",
        'timestamp':   ts.isoformat(),
        'user':        random.choice(USERS),
        'platform':    random.choice(PLATFORMS),
        'text':        random.choice(SAMPLE_TEXTS) + f" #{i}",
        'language':    random.choice(LANGUAGES),
        'sentiment':   random.choice(SENTIMENTS),
        'likes':       int(np.random.exponential(50)),
        'retweets':    int(np.random.exponential(10)),
        'replies':     int(np.random.exponential(5)),
        'hashtags':    random.sample(HASHTAGS, k=random.randint(1, 4)),
        'is_verified': random.random() < 0.05,
        'follower_count': int(np.random.pareto(1.5) * 100),
    }

N_POSTS = 50_000
jsonl_path = os.path.join(WORKDIR, 'social_media.jsonl')

print(f"Gerando {N_POSTS:,} posts...")
with open(jsonl_path, 'w', encoding='utf-8') as f:
    for i in range(N_POSTS):
        f.write(json.dumps(generate_post(i), ensure_ascii=False) + '\n')

jsonl_mb = os.path.getsize(jsonl_path) / 1e6
print(f"JSONL gerado: {jsonl_mb:.2f} MB")

# Preview
with open(jsonl_path) as f:
    sample = json.loads(f.readline())
print(f"\nExemplo de registro:")
print(json.dumps(sample, indent=2, ensure_ascii=False))

# ── 2. Detecção automática de schema ───────────────
# O `SchemaDetector` analisa uma amostra de documentos JSONL e:
# - Detecta tipos de cada campo
# - Normaliza campos aninhados (ex.: `user.name` → coluna plana)
# - Lida com campos ausentes

det = pf.SchemaDetector(sample_size=500)

with open(jsonl_path) as f:
    sample_docs = [json.loads(l) for l in f if l.strip()][:500]

schema_df, schema_info, stats = det.flatten(sample_docs)

print("Schema detectado:")
print(schema_df.dtypes)
print(f"\nColunas: {len(schema_df.columns)}")
print(f"Shape da amostra: {schema_df.shape}")
schema_df.head(3)

# ── 3. Compressão streaming com `freeze_file()` ────
# O `freeze_file()` processa o JSONL em chunks de 10k posts,
# nunca carregando o arquivo inteiro na memória.

pf_path = os.path.join(WORKDIR, 'social_media.permafrost')

posts_processed = [0]
def progress(rows, chunks, mb):
    posts_processed[0] = rows
    print(f"\r  Processando: {rows:,} posts | {chunks} chunks | {mb:.2f} MB", end='')

print("Comprimindo...")
metrics = pf.freeze_file(
    jsonl_path,
    pf_path,
    codec=pf.CODEC_LZMA2,
    chunk_rows=10_000,
    progress_cb=progress,
)
print()

pf_mb = os.path.getsize(pf_path) / 1e6

print(f"\n{'='*55}")
print(f"JSONL original:    {jsonl_mb:.2f} MB")
print(f".permafrost:       {pf_mb:.2f} MB")
print(f"Ratio:             {metrics['ratio']:.1f}×")
print(f"Redução:           {metrics['reduction_pct']:.1f}%")
print(f"Chunks:            {metrics['n_chunks']}")
print(f"Tempo:             {metrics['freeze_s']:.2f}s")

# ── 4. Leitura em streaming com `thaw_iter()` ──────
# Para processar datasets que não cabem na memória,
# `thaw_iter()` entrega um chunk por vez.

# Iterar sem carregar tudo na memória
total_likes = 0
total_rows  = 0
sentiment_counts = {'positive': 0, 'negative': 0, 'neutral': 0}

for chunk_df in pf.thaw_iter(pf_path, batch_size=5_000):
    total_likes += chunk_df['likes'].sum()
    total_rows  += len(chunk_df)
    if 'sentiment' in chunk_df.columns:
        for s in sentiment_counts:
            sentiment_counts[s] += (chunk_df['sentiment'] == s).sum()

print(f"Total posts processados: {total_rows:,}")
print(f"Total de likes:          {total_likes:,}")
print(f"Média de likes por post: {total_likes/total_rows:.1f}")
print(f"\nDistribuição de sentimento:")
for s, count in sentiment_counts.items():
    pct = count / total_rows * 100
    bar = '█' * int(pct / 2)
    print(f"  {s:10s}: {bar} {pct:.1f}%")

# ── 5. Calculadora de custo no AWS Glacier ─────────
# Quanto você economiza usando Permafrost antes de enviar para o Glacier?

# Preços AWS (USD/GB/mês)
GLACIER_DEEP = 0.00099   # $0.99 per TB

# Escalar para 1 ano de dados de social media
scale_factor = 365        # dias
jsonl_total_gb  = (jsonl_mb / 1e3) * scale_factor
pf_total_gb     = (pf_mb / 1e3) * scale_factor

custo_sem_pf  = jsonl_total_gb * GLACIER_DEEP
custo_com_pf  = pf_total_gb   * GLACIER_DEEP
economia_mes  = custo_sem_pf - custo_com_pf

print("Estimativa de custo — 1 ano de posts (50k/dia):")
print(f"\n  Sem Permafrost:")
print(f"    Tamanho total:  {jsonl_total_gb:.1f} GB")
print(f"    Custo/mês:      ${custo_sem_pf:.2f}")
print(f"\n  Com Permafrost:")
print(f"    Tamanho total:  {pf_total_gb:.2f} GB")
print(f"    Custo/mês:      ${custo_com_pf:.4f}")
print(f"\n  Economia:         ${economia_mes:.2f}/mês  ({(1-custo_com_pf/custo_sem_pf)*100:.0f}% menos)")
print(f"  Economia anual:   ${economia_mes*12:.2f}")

# ── Conclusão ──────────────────────────────────────
# JSONL é o formato onde o Permafrost brilha mais:
# - Overhead de chaves JSON repetidas → eliminado pelos preditores
# - Timestamps → `ts_delta_s` comprime deltas para quase zero
# - Strings categóricas (platform, sentiment, language) → `category_u8`
# - Inteiros com distribuição exponencial (likes, retweets) → `delta_zigzag`
# Resultado típico: **20–33× de compressão** vs `gzip` puro (5–6×).

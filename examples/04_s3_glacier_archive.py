"""
Exemplo 04 — Cloud Storage (S3 / GCS / Azure)
Demonstra freeze_to/thaw_from com LocalAdapter (drop-in para S3/GCS/Azure).
Executar: python examples/04_s3_glacier_archive.py
"""

# ──────────────────────────────────────────────────────────────────────
# ❄️ Permafrost — Cloud Storage (S3 / GCS / Azure)
# ──────────────────────────────────────────────────────────────────────
# Este notebook demonstra como usar o Permafrost com cloud storage:
# upload direto para S3, GCS ou Azure Blob, e lifecycle policy para
# Glacier Deep Archive.
# **Cenário:** 1 TB de dados históricos que devem ir para cold storage
# permanente com custo mínimo e integridade garantida.
# O que vamos fazer:
# 1. Freeze + upload em uma chamada (`freeze_to`)
# 2. Thaw direto da cloud (`thaw_from`)
# 3. Audit sem download (`audit_remote`)
# 4. Configurar lifecycle policy para Glacier
# 5. Calculadora de TCO — Total Cost of Ownership
# > **Nota:** As células de S3 requerem `boto3` e credenciais AWS.
# > As células com `[LOCAL]` funcionam sem nenhuma credencial.

# Para S3: %pip install permafrost-framework[s3] --quiet
# Para GCS: %pip install permafrost-framework[gcs] --quiet
# Para Azure: %pip install permafrost-framework[azure] --quiet

import permafrost as pf
import pandas as pd
import numpy as np
import os, tempfile

print(f"Permafrost {pf.__version__}")

import tempfile, os
WORKDIR = tempfile.mkdtemp(prefix='pf_demo_')
print(f'Arquivos temporários em: {WORKDIR}')

# ── 1. [LOCAL] freeze_to com LocalAdapter ──────────
# Funciona identicamente ao S3/GCS/Azure, mas grava localmente.
# Ótimo para desenvolver e testar a pipeline antes de apontar para cloud.

np.random.seed(42)
N = 100_000

df = pd.DataFrame({
    'timestamp':  pd.date_range('2023-01-01', periods=N, freq='1min'),
    'sensor_id':  np.random.randint(1, 1000, N),
    'temperatura': np.round(np.random.normal(25.0, 5.0, N), 2),
    'umidade':    np.round(np.random.uniform(30, 90, N), 1),
    'pressao':    np.round(np.random.normal(1013.25, 5, N), 2),
    'status':     np.random.choice(['ok', 'alerta', 'critico'], N, p=[0.9, 0.08, 0.02]),
    'localizacao': np.random.choice(['norte', 'sul', 'leste', 'oeste'], N),
})

print(f"Dataset: {len(df):,} linhas × {len(df.columns)} colunas")
df.head(3)

# Criar adapter local (simula S3/GCS/Azure)
local_storage = pf.LocalAdapter(os.path.join(WORKDIR, 'meu-bucket'))

# freeze_to: comprime + faz upload em uma chamada
metrics = pf.freeze_to(
    df,
    os.path.join(WORKDIR, 'meu-bucket', 'sensores', '2023', 'dados.permafrost'),
    adapter=local_storage,
    codec=pf.CODEC_LZMA2,
    partition_by='localizacao',
)

print(f"\nMetrics:")
print(f"  Ratio:           {metrics['ratio']:.2f}×")
print(f"  Tamanho:         {metrics['stored_mb']:.2f} MB")
print(f"  SHA-256 remoto:  {metrics.get('remote_magic_ok')}")
print(f"  Adapter:         {metrics.get('adapter')}")

# thaw_from: baixa + descomprime em uma chamada
df_back = pf.thaw_from(
    os.path.join(WORKDIR, 'meu-bucket', 'sensores', '2023', 'dados.permafrost'),
    adapter=local_storage,
    filter={'localizacao': 'norte'},
)

print(f"Linhas restauradas (só norte): {len(df_back):,}")
print(f"Localizações únicas: {df_back['localizacao'].unique().tolist()}")

# ── 2. [S3] Upload para AWS S3 ─────────────────────
# Substitua as variáveis abaixo com suas credenciais e bucket.
# Se não tiver credenciais AWS, pule para a seção 3.

# ─────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO — substitua com seus valores
S3_BUCKET  = 'meu-bucket-permafrost'
S3_PREFIX  = 'dados/sensores/2023/'
AWS_REGION = 'sa-east-1'   # São Paulo
# ─────────────────────────────────────────────────────────────────

# Verifica se boto3 está disponível
try:
    import boto3
    HAS_S3 = True
    print("boto3 disponível — células S3 vão executar")
except ImportError:
    HAS_S3 = False
    print("boto3 não instalado — execute: pip install permafrost-framework[s3]")
    print("As células S3 serão puladas.")

if HAS_S3:
    s3 = pf.S3Adapter(region=AWS_REGION)

    # Upload direto para S3
    remote_uri = f's3://{S3_BUCKET}/{S3_PREFIX}dados.permafrost'
    metrics_s3 = pf.freeze_to(
        df,
        remote_uri,
        adapter=s3,
        codec=pf.CODEC_LZMA2,
        partition_by='localizacao',
    )

    print(f"Upload para S3 concluído")
    print(f"  URI:    {remote_uri}")
    print(f"  Ratio:  {metrics_s3['ratio']:.2f}×")
    print(f"  Speed:  {metrics_s3['stored_mb'] / metrics_s3['upload_s']:.1f} MB/s")
else:
    print("[Pulado — boto3 não disponível]")

if HAS_S3:
    # Configurar lifecycle: mover para Glacier Deep Archive após 30 dias
    s3.set_lifecycle(
        bucket=S3_BUCKET,
        prefix=S3_PREFIX,
        transition_days=30,
        target_class='GLACIER_DEEP_ARCHIVE',
    )
    print(f"Lifecycle configurado: {S3_PREFIX} → Glacier após 30 dias")
else:
    print("[Pulado — boto3 não disponível]")

# ── 3. Calculadora TCO — Total Cost of Ownership ───
# Quanto você gasta em 3 anos com 1 TB de dados históricos?
# Comparativo: S3 Standard vs S3 Glacier vs Permafrost + Glacier.

# Preços AWS por GB/mês (USD)
PRECOS = {
    'S3 Standard':          0.023,
    'S3 Intelligent-Tiering': 0.023,  # camada frequent
    'S3 Glacier Instant':   0.004,
    'S3 Glacier Flexible':  0.0036,
    'S3 Glacier Deep':      0.00099,
}

RATIO_PERMAFROST = 10.0  # compressão média conservadora

TB = 1.0  # TB de dados originais
GB = TB * 1024
MESES = 36  # 3 anos

print(f"Dados originais: {TB} TB ({GB:.0f} GB)")
print(f"Com Permafrost ({RATIO_PERMAFROST:.0f}×): {GB/RATIO_PERMAFROST:.0f} GB")
print(f"Período: {MESES} meses ({MESES/12:.0f} anos)")
print()

resultados = []
for tier, preco in PRECOS.items():
    # Sem Permafrost
    custo_sem = GB * preco * MESES
    # Com Permafrost (só Deep Archive faz sentido como destino final)
    custo_com = (GB / RATIO_PERMAFROST) * PRECOS['S3 Glacier Deep'] * MESES
    economia  = custo_sem - custo_com

    resultados.append({
        'Tier': tier,
        'Sem Permafrost': f'${custo_sem:,.0f}',
        'Com Permafrost + Glacier': f'${custo_com:,.0f}',
        'Economia': f'${economia:,.0f}  ({economia/custo_sem*100:.0f}%)',
    })

df_tco = pd.DataFrame(resultados).set_index('Tier')
df_tco

# Cenário mais realista: crescimento mensal de 50 GB
CRESCIMENTO_GB_MES = 50

total_sem_pf = 0
total_com_pf = 0

for mes in range(1, MESES + 1):
    gb_acumulado = CRESCIMENTO_GB_MES * mes
    # Sem Permafrost: S3 Standard no primeiro mês, vai migrando para Glacier
    tier = PRECOS['S3 Glacier Deep'] if mes > 3 else PRECOS['S3 Standard']
    total_sem_pf += gb_acumulado * tier
    # Com Permafrost: sempre Glacier Deep desde o início
    total_com_pf += (gb_acumulado / RATIO_PERMAFROST) * PRECOS['S3 Glacier Deep']

print(f"Cenário: crescimento de {CRESCIMENTO_GB_MES} GB/mês por {MESES} meses")
print(f"\n  Sem Permafrost (Standard → Glacier): ${total_sem_pf:,.2f} total")
print(f"  Com Permafrost + Glacier Deep:        ${total_com_pf:,.2f} total")
print(f"\n  Economia total: ${total_sem_pf - total_com_pf:,.2f}  ({(1 - total_com_pf/total_sem_pf)*100:.0f}% de redução)")

# ── 4. GCS e Azure (referência rápida) ─────────────
# A API é idêntica — só muda o adapter e o prefixo da URI.

# Google Cloud Storage
# pip install permafrost-framework[gcs]
# gcs = pf.GCSAdapter(project='meu-projeto-gcp')
# metrics = pf.freeze_to(df, 'gs://meu-bucket/dados.permafrost', adapter=gcs)
# df_back = pf.thaw_from('gs://meu-bucket/dados.permafrost', adapter=gcs)

# Azure Blob Storage
# pip install permafrost-framework[azure]
# azure = pf.AzureAdapter(conn_str='DefaultEndpointsProtocol=https;...')
# metrics = pf.freeze_to(df, 'azure://container/dados.permafrost', adapter=azure)
# df_back = pf.thaw_from('azure://container/dados.permafrost', adapter=azure)

print("Exemplos comentados — descomente e preencha as credenciais para usar")

# ── Resumo — escolha da tier ───────────────────────
# | Cenário | Recomendação |
# |---------|-------------|
# | Dados quentes (<30 dias) | S3 Standard + Permafrost ZSTD |
# | Dados mornos (30–90 dias) | S3 Glacier Instant + Permafrost LZMA2 |
# | Dados frios (>90 dias) | S3 Glacier Deep Archive + Permafrost LZMA2 |
# | Compliance (7+ anos) | S3 Glacier Deep + lifecycle automático |
# Use `s3.set_lifecycle()` para automatizar a transição entre tiers.

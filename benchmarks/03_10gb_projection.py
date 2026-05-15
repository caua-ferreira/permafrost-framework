"""
Benchmark 03 — Simulação e projeção para 10 GB
Executa o pipeline PermafrostCodec v3 completo (L0→L4) e projeta os resultados
para 10 GB usando fator de escala medido.

Uso:
  python benchmarks/03_10gb_projection.py
"""
import sys, os, io, time, json, lzma
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import zstandard as zstd
import pyarrow as pa
import pyarrow.parquet as pq

TARGET_GB = 10.0

def generate(n=500_000, seed=42):
    np.random.seed(seed)
    products=[f'PROD-{i:05d}' for i in range(2000)]
    clients=[f'CLI-{i:06d}' for i in range(50000)]
    return pd.DataFrame({
        'id':             np.arange(1,n+1,dtype=np.int32),
        'data':           pd.date_range('2019-01-01',periods=n,freq='30s').strftime('%Y-%m-%d'),
        'hora':           pd.date_range('2019-01-01',periods=n,freq='30s').strftime('%H:%M:%S'),
        'cliente_id':     np.random.choice(clients,n),
        'produto_id':     np.random.choice(products,n),
        'categoria':      np.random.choice(['Eletrônicos','Vestuário','Alimentos','Automotivo','Saúde','Casa'],n),
        'quantidade':     np.random.randint(1,200,n,dtype=np.int16),
        'preco_unitario': np.round(np.random.uniform(1.99,14999.99,n),2),
        'desconto_pct':   np.round(np.random.choice([0,0.05,0.10,0.15,0.20,0.25,0.30],n),2),
        'total_bruto':    np.round(np.random.uniform(2,80000,n),2),
        'total_liquido':  np.round(np.random.uniform(2,75000,n),2),
        'frete':          np.round(np.random.choice([0,0,9.9,14.9,19.9,29.9],n),2),
        'pais':           np.random.choice(['Brasil','EUA','Argentina','Chile','México'],n),
        'estado':         np.random.choice(['SP','RJ','MG','RS','PR','SC','BA','GO','DF','CE'],n),
        'canal':          np.random.choice(['Online','Loja','Telefone','Parceiro','App'],n),
        'status':         np.random.choice(['Ativo','Inativo','Pendente','Cancelado','Aprovado'],n),
        'vendedor_id':    np.random.randint(1000,9999,n,dtype=np.int32),
        'filial_id':      np.random.randint(1,500,n,dtype=np.int16),
        'nota_fiscal':    [f'NF-{np.random.randint(1000000,9999999)}' for _ in range(n)],
        'score_cliente':  np.round(np.random.uniform(0,1000,n),1),
        'latitude':       np.round(np.random.uniform(-33,5,n),6),
        'longitude':      np.round(np.random.uniform(-73,-34,n),6),
        'observacao':     np.random.choice(['Entrega expressa','Pag. à vista','Parcelado 12x','VIP','OK','Normal'],n),
    })

def encode_semantic(df):
    df2 = df.copy()
    for col in ['id','vendedor_id']:
        df2[col] = df2[col].diff().fillna(df2[col].iloc[0]).astype(np.int32)
    df2['preco_unitario'] = (df2['preco_unitario']*100).astype(np.int32)
    df2['total_bruto']    = (df2['total_bruto']*100).astype(np.int32)
    df2['total_liquido']  = (df2['total_liquido']*100).astype(np.int32)
    df2['score_cliente']  = (df2['score_cliente']*10).astype(np.int32)
    df2['latitude']       = (df2['latitude']*1_000_000).astype(np.int32)
    df2['longitude']      = (df2['longitude']*1_000_000).astype(np.int32)
    for col in ['categoria','canal','status','pais','estado']:
        df2[col] = df2[col].astype('category').cat.codes.astype(np.int8)
    return df2

def run():
    print("=" * 65)
    print(f"PERMAFROST 10 GB PROJECTION — PermafrostCodec v3")
    print("=" * 65)

    print("\nGerando dataset de amostra (500k linhas, 23 colunas)...")
    df = generate()
    raw_csv = df.to_csv(index=False).encode()
    RS = len(raw_csv)
    SCALE = (TARGET_GB * 1e9) / RS
    print(f"Sample: {RS/1e6:.2f} MB | Fator de escala: {SCALE:.2f}x\n")

    pipeline = []

    # L0 — Deduplicação
    print("L0 — Deduplicação (20% duplicatas simuladas)...")
    np.random.seed(1)
    idx_dup = np.random.choice(df.index, int(len(df)*0.20), replace=False)
    df_dup = pd.concat([df, df.loc[idx_dup]]).sample(frac=1, random_state=7).reset_index(drop=True)
    raw_dup = df_dup.to_csv(index=False).encode()
    t0=time.time(); df_dd=df_dup.drop_duplicates(); t_dedup=time.time()-t0
    raw_dd = df_dd.to_csv(index=False).encode()
    ratio_l0 = len(raw_dup)/len(raw_dd)
    pipeline.append(('Entrada + 20% dup', len(raw_dup)/1e6*SCALE, ratio_l0, t_dedup*SCALE))
    pipeline.append(('L0 — Deduplicação', len(raw_dd)/1e6*SCALE, ratio_l0, t_dedup*SCALE))
    print(f"  {len(raw_dup)/1e6:.1f}MB → {len(raw_dd)/1e6:.1f}MB | ratio={ratio_l0:.3f}x")

    # L1 — Encoding semântico
    print("L1 — Encoding semântico...")
    df_enc = encode_semantic(df_dd)
    raw_enc = df_enc.to_csv(index=False).encode()
    ratio_l1 = len(raw_dd)/len(raw_enc)
    pipeline.append(('L1 — Encoding semântico', len(raw_enc)/1e6*SCALE, ratio_l1, 0))
    print(f"  {len(raw_dd)/1e6:.1f}MB → {len(raw_enc)/1e6:.1f}MB | ratio={ratio_l1:.3f}x")

    # L2 — Parquet colunar
    print("L2 — Parquet colunar (sem compressão interna)...")
    tbl = pa.Table.from_pandas(df_enc)
    buf = io.BytesIO()
    t0=time.time()
    pq.write_table(tbl, buf, compression='none', use_dictionary=True, row_group_size=256_000)
    t_pq=time.time()-t0
    pq_bytes = buf.getvalue()
    ratio_l2 = len(raw_enc)/len(pq_bytes)
    pipeline.append(('L2 — Parquet colunar', len(pq_bytes)/1e6*SCALE, ratio_l2, t_pq*SCALE))
    print(f"  {len(raw_enc)/1e6:.1f}MB → {len(pq_bytes)/1e6:.1f}MB | ratio={ratio_l2:.3f}x")

    # L3 — Zstd L19
    print("L3 — Zstd L19 externo...")
    cctx = zstd.ZstdCompressor(level=19, threads=2)
    t0=time.time(); c_zstd=cctx.compress(pq_bytes); t_zstd=time.time()-t0
    ratio_l3 = len(pq_bytes)/len(c_zstd)
    pipeline.append(('L3 — Zstd L19', len(c_zstd)/1e6*SCALE, ratio_l3, t_zstd*SCALE))
    print(f"  {len(pq_bytes)/1e6:.1f}MB → {len(c_zstd)/1e6:.1f}MB | ratio={ratio_l3:.3f}x")

    # L3b — LZMA2 extreme (alternativa)
    print("L3b — LZMA2 extreme (alternativa)...")
    t0=time.time()
    c_lzma = lzma.compress(pq_bytes, format=lzma.FORMAT_XZ, preset=lzma.PRESET_EXTREME|9)
    t_lzma=time.time()-t0
    ratio_l3b = len(pq_bytes)/len(c_lzma)
    pipeline.append(('L3b — LZMA2 extreme', len(c_lzma)/1e6*SCALE, ratio_l3b, t_lzma*SCALE))
    print(f"  {len(pq_bytes)/1e6:.1f}MB → {len(c_lzma)/1e6:.1f}MB | ratio={ratio_l3b:.3f}x")

    # Calcular ratio total
    total_input  = pipeline[0][1]  # entrada com duplicatas
    total_output = pipeline[-1][1] # LZMA2 output
    total_ratio  = total_input / total_output
    total_reduction = (1 - total_output/total_input) * 100

    print("\n" + "=" * 65)
    print(f"PROJEÇÃO FINAL PARA {TARGET_GB} GB")
    print("=" * 65)
    print(f"{'Estágio':35s} {'GB':>8} {'Ratio':>8}")
    print("-" * 55)
    for name, mb, ratio, _ in pipeline:
        print(f"  {name:33s} {mb/1024:>8.3f} {ratio:>7.3f}x")
    print("-" * 55)
    print(f"  {'RATIO TOTAL':33s} {'→':>8} {total_ratio:>7.2f}x")
    print(f"  {'REDUÇÃO TOTAL':33s} {'→':>8} {total_reduction:>6.1f}%")

    os.makedirs('benchmarks/results', exist_ok=True)
    out = {
        'target_gb': TARGET_GB,
        'sample_mb': RS/1e6,
        'scale_factor': SCALE,
        'pipeline': [{'stage':n,'gb':round(mb/1024,4),'mb':round(mb,2),'ratio':round(r,4)} for n,mb,r,_ in pipeline],
        'total_ratio': round(total_ratio, 3),
        'total_reduction_pct': round(total_reduction, 2),
    }
    with open('benchmarks/results/03_10gb_projection.json', 'w') as f:
        json.dump(out, f, indent=2)
    print("\n✓ Resultados salvos em benchmarks/results/03_10gb_projection.json")

if __name__ == '__main__':
    run()

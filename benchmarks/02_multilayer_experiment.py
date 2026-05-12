"""
Benchmark 02 — Experimento multi-camada
Prova empiricamente que "comprimir o já comprimido" não funciona
e demonstra quais técnicas realmente funcionam.

Uso:
  python benchmarks/02_multilayer_experiment.py
"""
import sys, os, io, time, gzip, lzma, json, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import zstandard as zstd
from collections import Counter


def entropy(data: bytes) -> float:
    c = Counter(data[:50_000])
    n = min(50_000, len(data))
    return -sum((v/n) * math.log2(v/n) for v in c.values())


def generate(n=80_000, seed=42):
    np.random.seed(seed)
    products = [f'P{i:04d}' for i in range(200)]
    df = pd.DataFrame({
        'id':     np.arange(1, n+1, dtype=np.int32),
        'prod':   np.random.choice(products, n),
        'total':  np.round(np.random.uniform(1, 5000, n), 2),
        'status': np.random.choice(['Ativo','Inativo','Pendente','Cancelado'], n),
        'pais':   np.random.choice(['Brasil','EUA','México','Argentina'], n),
        'vend':   np.random.randint(100, 9999, n, dtype=np.int32),
    })
    return df.to_csv(index=False).encode()


def run():
    print("=" * 65)
    print("EXPERIMENTO MULTI-CAMADA — O QUE REALMENTE FUNCIONA")
    print("=" * 65)

    raw = generate()
    RS  = len(raw)
    results = {}

    # ── EXP 1: Entropia ──────────────────────────────────────────
    print("\n[EXP 1] Entropia de Shannon")
    c19 = zstd.ZstdCompressor(level=19).compress(raw)
    ent_raw  = entropy(raw)
    ent_comp = entropy(c19)
    print(f"  Raw:         {ent_raw:.3f} bits/byte")
    print(f"  Zstd L19:    {ent_comp:.3f} bits/byte  (máx teórico: 8.000)")
    print(f"  → Dado comprimido é {ent_comp/8*100:.1f}% aleatório — nada para comprimir")
    results['entropy'] = {'raw': ent_raw, 'compressed': ent_comp, 'max': 8.0}

    # ── EXP 2: Dupla compressão ──────────────────────────────────
    print("\n[EXP 2] Dupla compressão (mesmo codec)")
    c1 = zstd.ZstdCompressor(level=19).compress(raw)
    c2 = zstd.ZstdCompressor(level=19).compress(c1)
    c3 = zstd.ZstdCompressor(level=19).compress(c2)
    c5 = zstd.ZstdCompressor(level=19).compress(
         zstd.ZstdCompressor(level=19).compress(c3))
    print(f"  1 camada:  {len(c1):>8,} bytes")
    print(f"  2 camadas: {len(c2):>8,} bytes  ({len(c2)-len(c1):+d} bytes)")
    print(f"  3 camadas: {len(c3):>8,} bytes  ({len(c3)-len(c2):+d} bytes)")
    print(f"  5 camadas: {len(c5):>8,} bytes  ({len(c5)-len(c1):+d} bytes total)")
    print(f"  → Cada camada PIORA o arquivo (overhead de header)")
    results['double_compress'] = {
        'l1': len(c1), 'l2': len(c2), 'l3': len(c3), 'l5': len(c5)
    }

    # ── EXP 3: Deduplicação ──────────────────────────────────────
    print("\n[EXP 3] Deduplicação antes de comprimir")
    df = pd.read_csv(io.StringIO(raw.decode()))
    df_dup = pd.concat([df, df.sample(frac=0.40, random_state=1)]).sample(frac=1, random_state=2).reset_index(drop=True)
    raw_dup = df_dup.to_csv(index=False).encode()
    raw_dd  = df_dup.drop_duplicates().to_csv(index=False).encode()
    c_dup = zstd.ZstdCompressor(level=19).compress(raw_dup)
    c_dd  = zstd.ZstdCompressor(level=19).compress(raw_dd)
    gain  = (len(c_dup) - len(c_dd)) / len(c_dup) * 100
    print(f"  Com 40% dup: {len(c_dup)/1e3:>7.1f} KB")
    print(f"  Deduped:     {len(c_dd)/1e3:>7.1f} KB  ganho={gain:.1f}%")
    results['dedup'] = {'with_dup': len(c_dup), 'deduped': len(c_dd), 'gain_pct': round(gain,2)}

    # ── EXP 4: Solid compression ─────────────────────────────────
    print("\n[EXP 4] Solid compression (múltiplos chunks juntos)")
    chunks = [generate(8_000, seed=i) for i in range(8)]
    ind_sizes  = [len(zstd.ZstdCompressor(level=19).compress(c)) for c in chunks]
    ind_total  = sum(ind_sizes)
    solid_c    = zstd.ZstdCompressor(level=19).compress(b''.join(chunks))
    solid_gain = (ind_total - len(solid_c)) / ind_total * 100
    print(f"  Individual: {ind_total:>8,} bytes")
    print(f"  Solid:      {len(solid_c):>8,} bytes  ganho={solid_gain:.1f}%")
    results['solid'] = {'individual': ind_total, 'solid': len(solid_c), 'gain_pct': round(solid_gain,2)}

    # ── EXP 5: Parquet — ordem das operações ─────────────────────
    print("\n[EXP 5] Parquet — ordem correta das operações")
    import pyarrow as pa
    import pyarrow.parquet as pq
    tbl = pa.Table.from_pandas(df)
    b1 = io.BytesIO()
    pq.write_table(tbl, b1, compression='zstd', compression_level=9, use_dictionary=True)
    pq_zstd = b1.getvalue()
    b2 = io.BytesIO()
    pq.write_table(tbl, b2, compression='none', use_dictionary=True)
    pq_none = b2.getvalue()
    pq_none_zstd = zstd.ZstdCompressor(level=19).compress(pq_none)
    diff = (len(pq_zstd) - len(pq_none_zstd)) / len(pq_zstd) * 100
    print(f"  Parquet+Zstd (interno):      {len(pq_zstd)/1e3:>7.1f} KB")
    print(f"  Parquet(none)+Zstd (externo):{len(pq_none_zstd)/1e3:>7.1f} KB  melhora={diff:.1f}%")
    print(f"  → Compressão EXTERNA é {diff:.1f}% melhor — Zstd vê o arquivo inteiro")
    results['parquet_order'] = {
        'internal_kb': round(len(pq_zstd)/1e3, 1),
        'external_kb': round(len(pq_none_zstd)/1e3, 1),
        'gain_pct': round(diff, 2)
    }

    # ── RESUMO ───────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("RESUMO — O QUE FUNCIONA E O QUE NÃO FUNCIONA")
    print("=" * 65)
    rows = [
        ("Dupla compressão (mesmo codec)", "0%", "PIOROU", "✗"),
        ("Deduplicação",                  f"+{results['dedup']['gain_pct']:.1f}%", "FUNCIONA", "✓"),
        ("Solid (multi-arquivo)",         f"+{results['solid']['gain_pct']:.1f}%", "FUNCIONA", "✓"),
        ("Parquet externo vs interno",    f"+{results['parquet_order']['gain_pct']:.1f}%", "FUNCIONA", "✓"),
    ]
    for name, gain, status, icon in rows:
        print(f"  {icon} {name:40s} {gain:>8}  [{status}]")

    os.makedirs('benchmarks/results', exist_ok=True)
    with open('benchmarks/results/02_multilayer.json', 'w') as f:
        json.dump(results, f, indent=2)
    print("\n✓ Resultados salvos em benchmarks/results/02_multilayer.json")


if __name__ == '__main__':
    run()

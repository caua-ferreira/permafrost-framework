"""
Benchmark 01 — Comparativo de algoritmos de compressão
Compara Zstd, LZMA2, Brotli, LZ4, gzip sobre dados tabulares corporativos.
Salva resultados em benchmarks/results/01_algorithms.json

Uso:
  python benchmarks/01_compression_algorithms.py
  python benchmarks/01_compression_algorithms.py --rows 200000 --scale-to-gb 1
"""
import sys, os, argparse, json, time, gzip, lzma, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import zstandard as zstd
import pyarrow as pa
import pyarrow.parquet as pq

try:
    import lz4.frame as lz4f
    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False


def generate_dataset(n_rows: int, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    products = [f'PROD-{i:05d}' for i in range(500)]
    clients  = [f'CLI-{i:06d}' for i in range(10000)]
    return pd.DataFrame({
        'id':             np.arange(1, n_rows+1, dtype=np.int32),
        'data':           pd.date_range('2019-01-01', periods=n_rows, freq='60s').strftime('%Y-%m-%d'),
        'cliente_id':     np.random.choice(clients, n_rows),
        'produto_id':     np.random.choice(products, n_rows),
        'categoria':      np.random.choice(['Eletrônicos','Vestuário','Alimentos','Automotivo','Saúde'], n_rows),
        'quantidade':     np.random.randint(1, 200, n_rows, dtype=np.int16),
        'preco_unitario': np.round(np.random.uniform(1.99, 14999.99, n_rows), 2),
        'total_liquido':  np.round(np.random.uniform(2, 75000, n_rows), 2),
        'pais':           np.random.choice(['Brasil','EUA','Argentina','Chile','México'], n_rows),
        'status':         np.random.choice(['Ativo','Inativo','Pendente','Cancelado'], n_rows),
        'vendedor_id':    np.random.randint(1000, 9999, n_rows, dtype=np.int32),
        'observacao':     np.random.choice(['OK','Urgente','Normal','VIP','Reenvio'], n_rows),
    })


def bench(name: str, compress_fn, data: bytes, raw_size: int) -> dict:
    t0 = time.time()
    compressed = compress_fn(data)
    tc = time.time() - t0
    c_size = len(compressed)
    ratio = raw_size / c_size
    speed = (raw_size / 1e6) / tc
    return {
        'name': name,
        'compressed_bytes': c_size,
        'ratio': round(ratio, 4),
        'comp_speed_mbs': round(speed, 2),
        'comp_time_s': round(tc, 3),
    }


def run(n_rows: int = 200_000, scale_to_gb: float = 1.0, seed: int = 42):
    print(f"Gerando dataset ({n_rows:,} linhas)...")
    df = generate_dataset(n_rows, seed)
    raw = df.to_csv(index=False).encode()
    raw_size = len(raw)
    scale = (scale_to_gb * 1e9) / raw_size

    print(f"Sample: {raw_size/1e6:.2f} MB | Scale para {scale_to_gb} GB: {scale:.2f}x\n")
    print(f"{'Algoritmo':35s} {'MB@target':>10} {'Ratio':>7} {'MB/s':>8}")
    print("-" * 65)

    results = [{'name': 'CSV raw', 'compressed_bytes': raw_size, 'ratio': 1.0, 'comp_speed_mbs': 0}]
    print(f"  {'CSV raw':33s} {raw_size/1e6*scale:>10.1f} {'1.00x':>7} {'—':>8}")

    benchmarks = [
        ('gzip L6',  lambda d: gzip.compress(d, compresslevel=6)),
        ('gzip L9',  lambda d: gzip.compress(d, compresslevel=9)),
        ('Zstd L3',  lambda d: zstd.ZstdCompressor(level=3).compress(d)),
        ('Zstd L9',  lambda d: zstd.ZstdCompressor(level=9).compress(d)),
        ('Zstd L19', lambda d: zstd.ZstdCompressor(level=19, threads=2).compress(d)),
        ('LZMA2 P6', lambda d: lzma.compress(d, format=lzma.FORMAT_XZ, preset=6)),
        ('LZMA2 P9E',lambda d: lzma.compress(d, format=lzma.FORMAT_XZ, preset=lzma.PRESET_EXTREME|9)),
    ]
    if HAS_LZ4:
        benchmarks.insert(0, ('LZ4', lambda d: lz4f.compress(d, compression_level=0)))
    if HAS_BROTLI:
        benchmarks += [('Brotli Q4', lambda d: brotli.compress(d, quality=4))]

    for name, fn in benchmarks:
        r = bench(name, fn, raw, raw_size)
        results.append(r)
        mb = r['compressed_bytes'] / 1e6 * scale
        print(f"  {name:33s} {mb:>10.1f} {r['ratio']:>6.2f}x {r['comp_speed_mbs']:>7.1f}")

    # Parquet + Zstd (colunar)
    print("\nParquet (colunar):")
    tbl = pa.Table.from_pandas(df)
    for pq_codec, pq_level in [('zstd', 3), ('zstd', 9), ('none', 0)]:
        buf = io.BytesIO()
        t0 = time.time()
        pq.write_table(tbl, buf, compression=pq_codec,
                       compression_level=pq_level if pq_level else None,
                       use_dictionary=True)
        tc = time.time() - t0
        sz = buf.tell()
        mb = sz / 1e6 * scale
        ratio = raw_size / sz
        label = f'Parquet+{pq_codec.upper()}{"" if not pq_level else f"-L{pq_level}"}'
        print(f"  {label:33s} {mb:>10.1f} {ratio:>6.2f}x {(raw_size/1e6)/tc:>7.1f}")
        results.append({'name': label, 'compressed_bytes': sz, 'ratio': round(ratio,4),
                        'comp_speed_mbs': round((raw_size/1e6)/tc, 2)})

    os.makedirs('benchmarks/results', exist_ok=True)
    out = {
        'n_rows': n_rows, 'raw_bytes': raw_size,
        'scale_factor': round(scale, 3), 'target_gb': scale_to_gb,
        'results': results
    }
    with open('benchmarks/results/01_algorithms.json', 'w') as f:
        json.dump(out, f, indent=2)
    print("\n✓ Resultados salvos em benchmarks/results/01_algorithms.json")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', type=int, default=200_000)
    parser.add_argument('--scale-to-gb', type=float, default=1.0)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    run(args.rows, args.scale_to_gb, args.seed)

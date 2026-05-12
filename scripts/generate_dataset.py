"""
Gerador de dataset corporativo sintético para benchmarks do Permafrost.

Uso:
  python scripts/generate_dataset.py --rows 80000 --output data/samples/test.csv
  python scripts/generate_dataset.py --rows 500000 --seed 42 --output data/samples/large.csv
"""
import argparse, os, sys
import numpy as np
import pandas as pd


def generate(n_rows: int = 80_000, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    products = [f'PROD-{i:05d}' for i in range(500)]
    clients  = [f'CLI-{i:06d}' for i in range(10000)]

    df = pd.DataFrame({
        'id':             np.arange(1, n_rows+1, dtype=np.int32),
        'data':           pd.date_range('2020-01-01', periods=n_rows, freq='5min'),
        'cliente_id':     np.random.choice(clients, n_rows),
        'produto_id':     np.random.choice(products, n_rows),
        'categoria':      np.random.choice(['Eletrônicos','Vestuário','Alimentos','Automotivo','Saúde'], n_rows),
        'quantidade':     np.random.randint(1, 200, n_rows, dtype=np.int16),
        'preco_unitario': np.round(np.random.uniform(1.99, 4999.99, n_rows), 2),
        'desconto_pct':   np.round(np.random.choice([0, 0.05, 0.10, 0.15, 0.20], n_rows), 2),
        'total_liquido':  np.round(np.random.uniform(2, 50000, n_rows), 2),
        'pais':           np.random.choice(['Brasil','EUA','Argentina','Chile','México'], n_rows),
        'estado':         np.random.choice(['SP','RJ','MG','RS','PR','SC','BA'], n_rows),
        'status':         np.random.choice(['Ativo','Inativo','Pendente','Cancelado'], n_rows),
        'vendedor_id':    np.random.randint(1000, 9999, n_rows, dtype=np.int32),
        'forma_pagto':    np.random.choice(['Boleto','Cartão Crédito','Pix','TED'], n_rows),
        'score_cliente':  np.round(np.random.uniform(0, 1000, n_rows), 1),
        'latitude':       np.round(np.random.uniform(-33, 5, n_rows), 6),
        'longitude':      np.round(np.random.uniform(-73, -34, n_rows), 6),
        'observacao':     np.random.choice(['Entrega expressa','Pag. à vista','Parcelado 12x','VIP','OK','Normal'], n_rows),
    })
    return df


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gera dataset corporativo sintético')
    parser.add_argument('--rows',   type=int, default=80_000, help='Número de linhas')
    parser.add_argument('--seed',   type=int, default=42,     help='Random seed')
    parser.add_argument('--output', type=str, default='data/samples/test.csv')
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    df = generate(args.rows, args.seed)
    df.to_csv(args.output, index=False)

    size_mb = os.path.getsize(args.output) / 1e6
    print(f"✓ Dataset gerado: {args.output}")
    print(f"  Linhas: {args.rows:,} | Colunas: {len(df.columns)} | Tamanho: {size_mb:.2f} MB")

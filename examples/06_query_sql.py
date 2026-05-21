"""
Exemplo 06 — Query SQL
Demonstra pf.query(): SQL sobre arquivos .permafrost/.pf sem descomprimir
manualmente, usando DuckDB como motor.

Executar: python examples/06_query_sql.py
"""
import permafrost as pf
import pandas as pd
import numpy as np
import tempfile, os

print(f"❄  Permafrost — Query SQL  (v{pf.__version__})\n")

TMP = tempfile.mkdtemp()
VENDAS = os.path.join(TMP, "vendas.pf")
METAS  = os.path.join(TMP, "metas.pf")

# ── Criar datasets ────────────────────────────────────────────────────────────
np.random.seed(42)
N = 100_000

df_vendas = pd.DataFrame({
    "id":        np.arange(1, N + 1, dtype="int32"),
    "data":      pd.date_range("2023-01-01", periods=N, freq="5min"),
    "ano":       pd.date_range("2023-01-01", periods=N, freq="5min").year,
    "mes":       pd.date_range("2023-01-01", periods=N, freq="5min").month,
    "regiao":    np.random.choice(["Norte", "Sul", "Leste", "Oeste"], N),
    "produto":   np.random.choice(["A", "B", "C", "D"], N),
    "vendedor":  np.random.choice([f"V{i:03d}" for i in range(1, 21)], N),
    "valor":     np.round(np.random.uniform(10, 5000, N), 2),
    "quantidade":np.random.randint(1, 20, N).astype("int32"),
})
df_vendas["receita"] = (df_vendas["valor"] * df_vendas["quantidade"]).round(2)

df_metas = pd.DataFrame({
    "regiao": ["Norte", "Sul", "Leste", "Oeste"],
    "meta_anual": [8_000_000, 12_000_000, 10_000_000, 9_000_000],
})

pf.freeze(df_vendas, VENDAS, partition_by="mes", codec=pf.CODEC_ZSTD)
pf.freeze(df_metas,  METAS,  codec=pf.CODEC_ZSTD)
print(f"Arquivos criados: {N:,} vendas + {len(df_metas)} regiões\n")

# ── 1. Agregação simples ──────────────────────────────────────────────────────
print("[1] Total de receita por região:")
df_q1 = pf.query(f"""
    SELECT regiao,
           COUNT(*)                    AS transacoes,
           ROUND(SUM(receita), 2)      AS receita_total,
           ROUND(AVG(valor), 2)        AS ticket_medio
    FROM '{VENDAS}'
    GROUP BY regiao
    ORDER BY receita_total DESC
""")
print(df_q1.to_string(index=False))

# ── 2. Filtro + ordenação ─────────────────────────────────────────────────────
print("\n[2] Top 5 vendedores (Sul) no 1º trimestre:")
df_q2 = pf.query(f"""
    SELECT vendedor,
           COUNT(*)               AS vendas,
           ROUND(SUM(receita), 2) AS receita
    FROM '{VENDAS}'
    WHERE regiao = 'Sul' AND mes <= 3
    GROUP BY vendedor
    ORDER BY receita DESC
    LIMIT 5
""")
print(df_q2.to_string(index=False))

# ── 3. Subquery / window function ────────────────────────────────────────────
print("\n[3] Receita por produto com % do total:")
df_q3 = pf.query(f"""
    SELECT produto,
           ROUND(SUM(receita), 2)                              AS receita,
           ROUND(100.0 * SUM(receita) / SUM(SUM(receita)) OVER (), 1) AS pct_total
    FROM '{VENDAS}'
    GROUP BY produto
    ORDER BY receita DESC
""")
print(df_q3.to_string(index=False))

# ── 4. JOIN entre dois arquivos .pf ──────────────────────────────────────────
print("\n[4] JOIN vendas × metas — % de atingimento:")
df_q4 = pf.query(f"""
    SELECT v.regiao,
           ROUND(SUM(v.receita), 0)        AS receita_total,
           m.meta_anual,
           ROUND(100.0 * SUM(v.receita) / m.meta_anual, 1) AS pct_meta
    FROM '{VENDAS}' v
    JOIN '{METAS}'  m ON v.regiao = m.regiao
    GROUP BY v.regiao, m.meta_anual
    ORDER BY pct_meta DESC
""")
print(df_q4.to_string(index=False))

# ── 5. Output direto como CSV / XLSX ─────────────────────────────────────────
print("\n[5] Exportar resultado como CSV e XLSX...")
csv_bytes  = pf.unfreeze(VENDAS, filter={"mes": 1}, output_format="csv", sep=";")
xlsx_bytes = pf.unfreeze(VENDAS, filter={"mes": 1}, output_format="xlsx")
print(f"  Janeiro CSV  (sep=';'): {len(csv_bytes):,} bytes")
print(f"  Janeiro XLSX:           {len(xlsx_bytes):,} bytes")

print("\n✓ Exemplo query SQL concluído!")

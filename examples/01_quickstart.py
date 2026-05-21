"""
Exemplo 01 — Quick Start v1.2.2
Demonstra o ciclo completo: freeze → audit → unfreeze → unfreeze seletivo
→ output (csv/xlsx) → append → diff → query SQL.

Executar: python examples/01_quickstart.py
"""
import permafrost as pf
import pandas as pd
import numpy as np
import tempfile, os

print(f"❄  Permafrost Quick Start  (v{pf.__version__})\n")

TMP = tempfile.mkdtemp()
PATH   = os.path.join(TMP, "vendas.pf")
PATH_V2 = os.path.join(TMP, "vendas_v2.pf")

# ── 1. Criar dataset ──────────────────────────────────────────────────────────
np.random.seed(42)
N = 50_000
df = pd.DataFrame({
    "id":     np.arange(1, N + 1, dtype="int32"),
    "data":   pd.date_range("2022-01-01", periods=N, freq="30min"),
    "ano":    pd.date_range("2022-01-01", periods=N, freq="30min").year,
    "regiao": np.random.choice(["Norte", "Sul", "Leste", "Oeste"], N),
    "total":  np.round(np.random.uniform(1, 50_000, N), 2),
    "status": np.random.choice(["Ativo", "Cancelado", "Pendente"], N),
})
df = df.sort_values("ano").reset_index(drop=True)
csv_mb = len(df.to_csv(index=False).encode()) / 1e6
print(f"Dataset: {N:,} linhas × {len(df.columns)} colunas = {csv_mb:.2f} MB CSV")

# ── 2. Freeze ─────────────────────────────────────────────────────────────────
print("\n[1] Freeze...")
m = pf.freeze(df, PATH,
               codec=pf.CODEC_LZMA2,
               partition_by="ano",
               primary_key="id",
               comment="Exemplo quick start")

print(f"  Original:  {m['original_mb']:.2f} MB")
print(f"  Resultado: {m['stored_mb']:.3f} MB")
print(f"  Ratio:     {m['ratio']:.2f}×")
print(f"  Redução:   {m['reduction_pct']:.1f}%")
print(f"  Tempo:     {m['freeze_s']:.2f}s")

# ── 3. Audit sem descomprimir ─────────────────────────────────────────────────
print("\n[2] Audit (sem descomprimir)...")
info = pf.audit(PATH)
print(f"  Codec:    {info['codec']}")
print(f"  Linhas:   {info['orig_rows']:,}")
print(f"  Chunks:   {info['n_chunks']}")
print(f"  Anos:     {info.get('partition_keys', [])}")

# ── 4. Unfreeze completo ──────────────────────────────────────────────────────
print("\n[3] Unfreeze completo...")
df_back = pf.unfreeze(PATH, verify=True)
print(f"  Linhas: {len(df_back):,}")

# ── 5. Unfreeze seletivo ──────────────────────────────────────────────────────
print("\n[4] Unfreeze seletivo (sparse index)...")
df_2023 = pf.unfreeze(PATH, filter={"ano": 2023})
print(f"  Linhas (ano=2023): {len(df_2023):,}")

# ── 6. Output CSV com separador customizado ───────────────────────────────────
print("\n[5] Output CSV (sep=';') e XLSX...")
csv_bytes = pf.unfreeze(PATH, filter={"ano": 2023}, output_format="csv", sep=";")
print(f"  CSV bytes: {len(csv_bytes):,}  (sep=';')")

xlsx_bytes = pf.unfreeze(PATH, filter={"ano": 2023}, output_format="xlsx")
print(f"  XLSX bytes: {len(xlsx_bytes):,}")

# ── 7. Append incremental ─────────────────────────────────────────────────────
print("\n[6] Append (escrita incremental)...")
np.random.seed(99)
df_new = pd.DataFrame({
    "id":     np.arange(N + 1, N + 1001, dtype="int32"),
    "data":   pd.date_range("2025-01-01", periods=1000, freq="1h"),
    "ano":    [2025] * 1000,
    "regiao": np.random.choice(["Norte", "Sul", "Leste", "Oeste"], 1000),
    "total":  np.round(np.random.uniform(1, 50_000, 1000), 2),
    "status": np.random.choice(["Ativo", "Cancelado", "Pendente"], 1000),
})
result = pf.append(PATH, df_new)
print(f"  Total linhas após append: {result['total_rows']:,}")
print(f"  Novos chunks:             {result['new_chunks']}")

# ── 8. Diff entre versões ─────────────────────────────────────────────────────
print("\n[7] Diff entre versões...")
# Criar v2 com alterações simuladas
df_v2 = df.copy()
df_v2.loc[0, "total"] = 99999.99    # preço alterado
df_v2.loc[1, "status"] = "Cancelado" # status alterado
df_v2 = df_v2.iloc[2:]              # remover 2 primeiras linhas (deleted)
pf.freeze(df_v2, PATH_V2, partition_by="ano", primary_key="id")

resultado = pf.diff(PATH, PATH_V2, output="summary")
print(f"  Deletados:  {resultado['deleted']}")
print(f"  Inseridos:  {resultado['inserted']}")
print(f"  Alterados:  {resultado['changed']}")
print(f"  Iguais:     {resultado['unchanged']}")

# ── 9. Query SQL ──────────────────────────────────────────────────────────────
print("\n[8] Query SQL...")
df_query = pf.query(f"SELECT regiao, COUNT(*) as total, AVG(total) as media FROM '{PATH}' GROUP BY regiao ORDER BY total DESC")
print(df_query.to_string(index=False))

print("\n✓ Quick start v1.2.2 concluído!")

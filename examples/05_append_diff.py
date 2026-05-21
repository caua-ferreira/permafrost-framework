"""
Exemplo 05 — Append & Diff
Demonstra escrita incremental com pf.append() e comparação de versões com pf.diff().

Executar: python examples/05_append_diff.py
"""
import permafrost as pf
import pandas as pd
import numpy as np
import tempfile, os

print(f"❄  Permafrost — Append & Diff  (v{pf.__version__})\n")

TMP = tempfile.mkdtemp()
V1 = os.path.join(TMP, "clientes_v1.pf")
V2 = os.path.join(TMP, "clientes_v2.pf")

# ── Dataset base ──────────────────────────────────────────────────────────────
np.random.seed(42)
N = 10_000
df_base = pd.DataFrame({
    "id":     np.arange(1, N + 1, dtype="int32"),
    "nome":   [f"Cliente {i}" for i in range(1, N + 1)],
    "plano":  np.random.choice(["free", "pro", "enterprise"], N),
    "mrr":    np.round(np.random.uniform(0, 500, N), 2),
    "ativo":  np.random.choice([True, False], N),
})

print(f"Dataset base: {N:,} clientes\n")

# ── 1. Freeze v1 ──────────────────────────────────────────────────────────────
pf.freeze(df_base, V1, codec=pf.CODEC_ZSTD, primary_key="id", comment="snapshot 2025-01")
info = pf.audit(V1)
print(f"[1] V1 gravado: {info['orig_rows']:,} linhas, {info['n_chunks']} chunks")

# ── 2. Append de novos clientes ───────────────────────────────────────────────
np.random.seed(99)
n_new = 500
df_new = pd.DataFrame({
    "id":     np.arange(N + 1, N + n_new + 1, dtype="int32"),
    "nome":   [f"Cliente {i}" for i in range(N + 1, N + n_new + 1)],
    "plano":  np.random.choice(["free", "pro", "enterprise"], n_new),
    "mrr":    np.round(np.random.uniform(0, 500, n_new), 2),
    "ativo":  [True] * n_new,
})

res = pf.append(V1, df_new)
print(f"\n[2] Append de {n_new} novos clientes:")
print(f"  Total após append: {res['total_rows']:,} linhas")
print(f"  Novos chunks:      {res['new_chunks']}")

# Verificar que os dados originais estão intactos
df_orig = pf.unfreeze(V1, filter={"id": 1})
assert len(df_orig) > 0, "dado original perdido!"
print(f"  Dados originais intactos ✓")

# ── 3. Criar snapshot v2 com mudanças ─────────────────────────────────────────
# Simular changes: upgrade de plano, alteração de mrr, churn
df_v2 = df_base.copy()
upgrades = np.random.choice(df_v2.index[df_v2["plano"] == "free"], 200, replace=False)
df_v2.loc[upgrades, "plano"] = "pro"
df_v2.loc[upgrades, "mrr"]   = np.round(np.random.uniform(49, 149, 200), 2)

churned = np.random.choice(df_v2.index, 150, replace=False)
df_v2.loc[churned, "ativo"] = False

# Remover 50 clientes (deleted)
df_v2 = df_v2.drop(df_v2.index[:50]).reset_index(drop=True)

# Adicionar 80 novos clientes (inserted)
df_inserted = pd.DataFrame({
    "id":    np.arange(N + n_new + 1, N + n_new + 81, dtype="int32"),
    "nome":  [f"Cliente {i}" for i in range(N + n_new + 1, N + n_new + 81)],
    "plano": ["pro"] * 80,
    "mrr":   np.round(np.random.uniform(49, 149, 80), 2),
    "ativo": [True] * 80,
})
df_v2 = pd.concat([df_v2, df_inserted], ignore_index=True)
pf.freeze(df_v2, V2, codec=pf.CODEC_ZSTD, primary_key="id", comment="snapshot 2025-02")

# ── 4. Diff entre v1 e v2 ────────────────────────────────────────────────────
print(f"\n[3] Diff V1 → V2:")
result = pf.diff(V1, V2, output="dict")

summary = result["summary"]
print(f"  Deletados:  {summary['deleted']:>5,}  (clientes que saíram)")
print(f"  Inseridos:  {summary['inserted']:>5,}  (clientes novos)")
print(f"  Alterados:  {summary['changed']:>5,}  (upgrades/churns)")
print(f"  Iguais:     {summary['unchanged']:>5,}")

# Inspecionar quais colunas mudaram nos registros alterados
if len(result["changed"]) > 0:
    changed_df = result["changed"]
    cols_mudadas = [c.replace("_v2", "") for c in changed_df.columns
                    if c.endswith("_v2")]
    print(f"\n  Colunas que mudaram: {cols_mudadas}")
    print(f"\n  Primeiros 5 alterados:")
    print(changed_df[["id"] + [c for c in changed_df.columns if c != "id"]].head(5).to_string(index=False))

# ── 5. Diff em formato dataframe ─────────────────────────────────────────────
print(f"\n[4] Diff como DataFrame (com coluna _diff):")
df_diff = pf.diff(V1, V2, output="dataframe")
print(df_diff.groupby("_diff").size().to_string())

print("\n✓ Exemplo append & diff concluído!")

"""
Exemplo 01 — Quick Start
Demonstra o ciclo completo: freeze → audit → thaw → thaw seletivo.
Executar: python examples/01_quickstart.py
"""
import permafrost as pf
import pandas as pd
import numpy as np

print("❄  Permafrost Quick Start\n")

# ── 1. Criar dataset ──────────────────────────────────────────────────────────
np.random.seed(42)
N = 50_000
df = pd.DataFrame({
    "id":     np.arange(1, N+1, dtype="int32"),
    "data":   pd.date_range("2022-01-01", periods=N, freq="30min"),
    "ano":    pd.date_range("2022-01-01", periods=N, freq="30min").year,
    "regiao": np.random.choice(["Norte","Sul","Leste","Oeste"], N),
    "total":  np.round(np.random.uniform(1, 50000, N), 2),
    "status": np.random.choice(["Ativo","Cancelado","Pendente"], N),
})
df = df.sort_values("ano").reset_index(drop=True)
csv_mb = len(df.to_csv(index=False).encode()) / 1e6
print(f"Dataset: {N:,} linhas × {len(df.columns)} colunas = {csv_mb:.2f} MB CSV")

# ── 2. Freeze ─────────────────────────────────────────────────────────────────
print("\n[1] Freeze (compressão)...")
m = pf.freeze(df, "/tmp/quickstart.permafrost",
              codec=pf.CODEC_LZMA2,
              partition_by="ano",
              comment="Exemplo quick start")

print(f"  Original:  {m['original_mb']:.2f} MB")
print(f"  Resultado: {m['stored_mb']:.3f} MB")
print(f"  Ratio:     {m['ratio']:.2f}×")
print(f"  Redução:   {m['reduction_pct']:.1f}%")
print(f"  Tempo:     {m['freeze_s']:.2f}s")

# ── 3. Audit sem descomprimir ─────────────────────────────────────────────────
print("\n[2] Audit (sem descomprimir)...")
info = pf.audit("/tmp/quickstart.permafrost")
print(f"  Codec:      {info['codec']}")
print(f"  Linhas:     {info['orig_rows']:,}")
print(f"  Chunks:     {info['n_chunks']}")
print(f"  Partição:   {info['partition_col']}")
print(f"  Anos:       {info['partition_keys']}")
print(f"  Freeze em:  {info['freeze_date']}")
print(f"  Comentário: {info['comment']}")

# ── 4. Thaw completo ──────────────────────────────────────────────────────────
print("\n[3] Thaw completo...")
df_back = pf.thaw("/tmp/quickstart.permafrost", verify=True)
print(f"  Linhas: {len(df_back):,}")
id_ok   = (df["id"].values == df_back["id"].values[:N].astype("int64")).all()
st_ok   = (df["status"].values == df_back["status"].astype(str).values[:N]).mean() * 100
fl_diff = abs(df["total"].values - df_back["total"].values[:N].astype(float)).max()
print(f"  IDs exatos:    {'✓' if id_ok else '✗'}")
print(f"  Status match:  {st_ok:.0f}%")
print(f"  Float max_diff: {fl_diff:.6f}")

# ── 5. Thaw seletivo ──────────────────────────────────────────────────────────
print("\n[4] Thaw seletivo (sparse index)...")
import os
file_size = os.path.getsize("/tmp/quickstart.permafrost")
for ano in sorted(df["ano"].unique()):
    df_ano = pf.thaw("/tmp/quickstart.permafrost", filter={"ano": ano})
    chunks_ano = [e for e in info["index_entries"] if str(ano) in e["part_key"]]
    pct = sum(e["byte_len"]+32 for e in chunks_ano) / file_size * 100
    print(f"  ano={ano}: {len(df_ano):,} linhas | {pct:.0f}% do arquivo lido")

print("\n✓ Quick start concluído!")

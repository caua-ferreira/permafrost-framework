"""
Exemplo 03 — Streaming: dataset maior que a RAM
Demonstra freeze_stream e peek com RAM constante.
Executar: python examples/03_streaming_large_dataset.py
"""
import permafrost as pf
import pandas as pd, numpy as np, time

print("❄  Permafrost — Streaming (datasets > RAM)\n")

TOTAL_ROWS = 300_000
BLOCK_SIZE = 50_000

def gerar_blocos(total=TOTAL_ROWS, block=BLOCK_SIZE):
    """Simula um cursor de banco de dados — gera blocos sem carregar tudo."""
    for start in range(0, total, block):
        n = min(block, total-start)
        np.random.seed(start//block)
        yield pd.DataFrame({
            "id":     np.arange(start+1, start+n+1, dtype="int32"),
            "total":  np.round(np.random.uniform(1, 50000, n), 2),
            "status": np.random.choice(["Ativo","Cancelado","Pendente"], n),
            "canal":  np.random.choice(["Online","Loja","App"], n),
            "regiao": np.random.choice(["Norte","Sul","Leste","Oeste"], n),
        })

print(f"Dataset: {TOTAL_ROWS:,} linhas em blocos de {BLOCK_SIZE:,}")
print(f"RAM por bloco: ~{BLOCK_SIZE*5*8/1e6:.0f} MB (constante)\n")

# Progress callback
def on_progress(rows, chunks, mb):
    if chunks % 2 == 0:
        print(f"  Progress: {rows:>7,} linhas | chunk {chunks:>2} | {mb:.1f} MB escritos")

# Freeze em streaming
print("[1] freeze_stream()...")
t0 = time.time()
m = pf.freeze_stream(
    gerar_blocos(),
    "/tmp/streaming_demo.permafrost",
    codec=pf.CODEC_LZMA2,
    progress_cb=on_progress,
)
t_freeze = time.time()-t0

print(f"\n  {m['rows']:,} linhas | {m['stored_mb']:.3f}MB | ratio={m['ratio']:.2f}× | {t_freeze:.2f}s")

# Thaw completo
print("\n[2] thaw() completo...")
t0 = time.time()
df_full = pf.unfreeze("/tmp/streaming_demo.permafrost", verify=True)
print(f"  {len(df_full):,} linhas em {time.time()-t0:.3f}s")
assert df_full["id"].iloc[0] == 1 and df_full["id"].iloc[-1] == TOTAL_ROWS

# Thaw iterativo — RAM constante
print("\n[3] peek() — sem carregar tudo...")
total_batches = 0; total_rows = 0
for batch in pf.peek("/tmp/streaming_demo.permafrost", batch_size=30_000):
    total_batches += 1; total_rows += len(batch)
print(f"  {total_batches} batches de 30k = {total_rows:,} linhas ✓")

print("\n✓ Exemplo streaming concluído!")

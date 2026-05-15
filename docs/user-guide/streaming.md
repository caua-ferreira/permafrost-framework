# Streaming — Datasets Maiores que a RAM

O Chunk Mode processa qualquer volume com **RAM constante**.
Não carrega o dataset inteiro na memória — apenas 1 bloco de cada vez.

---

## freeze_file() — arquivo grande

A forma mais simples. Lê o arquivo em blocos internamente:

```python
import permafrost as pf

# CSV de qualquer tamanho — sem carregar na RAM
metrics = pf.freeze_file(
    "100gb_vendas.csv",
    "100gb_vendas.permafrost",
    codec=pf.CODEC_LZMA2,
    chunk_rows=50_000,       # linhas por bloco (controla RAM)
    partition_by="ano",
)

print(f"{metrics['rows']:,} linhas")
print(f"RAM usada: ~{metrics.get('peak_ram_mb', '~200')} MB (constante)")
```

**Formatos suportados:** `.csv`, `.jsonl`, `.parquet`

---

## freeze_stream() — iterator customizado

Para fontes de dados que não são arquivos: cursores de banco, APIs, streams Kafka:

```python
import permafrost as pf
import pandas as pd

# Exemplo 1: cursor de banco de dados
def cursor_generator(connection, query, batch_size=50_000):
    offset = 0
    while True:
        df = pd.read_sql(
            f"{query} LIMIT {batch_size} OFFSET {offset}",
            connection
        )
        if len(df) == 0:
            break
        yield df
        offset += batch_size

metrics = pf.freeze_stream(
    cursor_generator(conn, "SELECT * FROM pedidos WHERE ano = 2024"),
    "pedidos_2024.permafrost",
    codec=pf.CODEC_LZMA2,
    partition_by="mes",
)
```

```python
# Exemplo 2: gerador Python simples
def meu_gerador():
    for arquivo in os.listdir("/data/chunks/"):
        yield pd.read_csv(f"/data/chunks/{arquivo}")

pf.freeze_stream(meu_gerador(), "consolidado.permafrost")
```

```python
# Exemplo 3: com progress callback
def on_progress(rows_done, chunks_done, mb_written):
    print(f"\r{rows_done:,} linhas | {chunks_done} chunks | {mb_written:.1f}MB", end="")

pf.freeze_stream(
    meu_iterator,
    "saida.permafrost",
    progress_cb=on_progress,
)
```

---

## thaw_iter() — iterar sem carregar tudo

```python
import permafrost as pf

# Iterar chunk por chunk (RAM = 1 chunk de cada vez)
for chunk_df in pf.thaw_iter("arquivo.permafrost"):
    processar(chunk_df)

# Iterar em batches de tamanho fixo
for batch in pf.thaw_iter("arquivo.permafrost", batch_size=25_000):
    print(f"Batch com {len(batch):,} linhas")

# Iterar com filtro — lê só chunks relevantes
for batch in pf.thaw_iter("arquivo.permafrost",
                            filter={"ano": 2024},
                            batch_size=50_000):
    processar_2024(batch)
```

---

## Comparação de RAM

| Abordagem | Dataset 10 GB | RAM necessária |
|-----------|---------------|----------------|
| `pd.read_csv()` + `pf.freeze()` | 10 GB | **~15–20 GB** |
| `pf.freeze_file()` | 10 GB | **~200 MB** |
| `pf.freeze_stream()` | ilimitado | **~200 MB** |

!!! warning "Manifesto detectado uma vez"
    No `freeze_stream`, o schema (preditores por coluna) é detectado no **primeiro bloco**
    e fixado para todos os blocos seguintes. Isso garante consistência — todos os chunks
    usam o mesmo preditor, independente da cardinalidade local de cada bloco.

---

## Como funciona internamente (two-pass)

```
PASS 1 — Comprimir blocos
  ┌─────────────────────────────────────┐
  │ block_0 → encode → compress → temp │
  │ block_1 → encode → compress → temp │
  │ ...                                 │
  │ block_N → encode → compress → temp │
  └─────────────────────────────────────┘
         (RAM: apenas 1 bloco por vez)

PASS 2 — Montar arquivo final
  ┌─────────────────────────────────────┐
  │ header (com offsets reais)          │
  │ payload (cópia do temp, 1MB/vez)   │
  │ sparse index (footer)               │
  └─────────────────────────────────────┘
```

O arquivo final é idêntico ao gerado por `freeze()` — o thaw não precisa saber
se o arquivo foi criado em streaming ou não.

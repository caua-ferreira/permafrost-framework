# Resultados dos Benchmarks

Os arquivos JSON neste diretório são gerados automaticamente pelos scripts de benchmark.

## Como gerar

```bash
bash scripts/run_benchmarks.sh
```

## Arquivos

| Arquivo | Script | Descrição |
|---|---|---|
| `01_algorithms.json` | `01_compression_algorithms.py` | Comparativo de algoritmos |
| `02_multilayer.json` | `02_multilayer_experiment.py` | Experimentos multi-camada |
| `03_10gb_projection.json` | `03_10gb_projection.py` | Projeção para 10 GB |

## Resultados de referência (medidos em 2026-05)

### Benchmark 01 — Algoritmos (80k linhas, projeção 1 GB)

| Algoritmo | Tamanho | Ratio |
|---|---|---|
| CSV raw | 1000 MB | 1.00× |
| LZ4 | 480 MB | 2.08× |
| gzip L9 | 279 MB | 3.58× |
| Zstd L9 | 272 MB | 3.67× |
| Zstd L19 | 232 MB | 4.32× |
| LZMA2 extreme | 223 MB | 4.48× |
| Parquet+Zstd L9 | 226 MB | 4.42× |
| **PermafrostCodec** | **215 MB** | **4.64×** |

### Benchmark 03 — Projeção 10 GB (pipeline completo)

| Estágio | GB | Redução |
|---|---|---|
| Entrada + 20% dup | 11.72 GB | — |
| L0 — Deduplicação | 9.77 GB | −16.7% |
| L1 — Encoding semântico | 7.89 GB | −19.2% |
| L2 — Parquet colunar | 3.96 GB | −49.8% |
| L3 — Zstd L19 | 2.33 GB | −41.1% |
| **L3b — LZMA2 extreme** | **1.75 GB** | **−24.9%** |

**Ratio total: ~6.7× | Redução: ~85%**

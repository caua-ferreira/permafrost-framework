# Arquitetura do Permafrost Data Framework

## Visão Geral

O Permafrost é inspirado na arquitetura do Apache Spark:
um coordenador central (Master), workers paralelos, e uma API unificada para o usuário.

## Componentes Planejados

```
┌─────────────────────────────────────────────────────────────┐
│  CLIENTE                                                     │
│  PermafrostSession.freeze(df, path, codec, quant)           │
│  PermafrostSession.thaw(path, filter)                       │
│  PermafrostCatalog.search(schema, period)                   │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST API
┌──────────────────────────▼──────────────────────────────────┐
│  PERMAFROST MASTER                                           │
│  • Recebe jobs de freeze/thaw                               │
│  • Faz scheduling por afinidade de dados                    │
│  • HA com Raft consensus (Apache Ratis)                     │
│  • Mantém estado de jobs (checkpointing)                    │
└──────────┬──────────────────────────────┬───────────────────┘
           │                              │
┌──────────▼──────────┐      ┌────────────▼───────────────────┐
│  PERMAFROST WORKER  │      │  PERMAFROST CATALOG SERVICE     │
│  • Executa L0→L4    │      │  • DuckDB local                 │
│  • Paralelismo por  │      │  • Indexa todos os .permafrost  │
│    chunk (256 MB)   │      │  • Schema, localização, custo   │
│  • Checkpointing    │      │  • API de busca por metadados   │
└──────────┬──────────┘      └────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│  STORAGE ADAPTER (interface plugável)                        │
│  S3Adapter | GCSAdapter | AzureAdapter | HDFSAdapter        │
└─────────────────────────────────────────────────────────────┘
```

## Pipeline de Compressão (PermafrostCodec v3)

```
DataFrame/CSV
    │
    ▼  L0: SHA-256 row hash → drop_duplicates()
    │     Elimina redundância lógica (-16.7% medido)
    │
    ▼  L1: Delta + Dict + Float→Int + Zigzag
    │     Elimina redundância semântica (-19.2%)
    │
    ▼  L2: Parquet colunar (compression=NONE)
    │     Elimina redundância estrutural (-49.8%)
    │     ↑ MAIOR ganho individual do pipeline
    │
    ▼  L3: Preditor colunar por tipo
    │     lag1_zigzag para floats
    │     delta_zigzag para IDs
    │     ts_delta_s para timestamps
    │     category_u8 para enums
    │
    ▼  L4: LZMA2 extreme | ZPAQ method=5
          Elimina redundância estatística (-41%+)
          Único layer de compressão real
          
    = arquivo .permafrost
```

## Analogia com Apache Spark

| Apache Spark | Permafrost |
|---|---|
| SparkContext | PermafrostContext |
| RDD / DataFrame | Permafrost Dataset (.permafrost) |
| Spark Master | PermafrostMaster |
| Spark Executor | PermafrostWorker |
| Catalyst Optimizer | Codec Auto-Selector (v2.0) |
| Spark SQL + pushdown | Thaw Query API + sparse index |
| DataSource API v2 | StorageAdapter Plugin API |
| spark-submit | `permafrost freeze / thaw` CLI |

## Formato do Arquivo

Ver [FORMAT_SPEC.md](FORMAT_SPEC.md) para a especificação completa.

```
[MAGIC "PRMS"][VERSION][FLAGS][CODEC][QUANT]
[ARROW SCHEMA][PREDICTOR MANIFEST][COMMENT]
[FREEZE_TS][RETENTION][ROWS][BYTES][PAYLOAD_LEN]
[HEADER SHA-256]
[PAYLOAD COMPRIMIDO]
[PAYLOAD SHA-256]
[EOF MAGIC "SMRP"]
```

## Decisões de Design

### Por que Parquet sem compressão interna?

Quando o Parquet comprime cada coluna internamente, gera ilhas de bytes
aleatorizados. O LZMA2/Zstd externo recebe um mosaico e não encontra
padrões cross-coluna.

Com `compression='none'`, o LZMA2 externo vê o arquivo como um único
stream — encontra padrões que cruzam row-groups inteiros.

**Resultado medido:** Parquet(none)+Zstd19 é **19.7% melhor** que Parquet+Zstd9 interno.

### Por que não comprimir o comprimido?

Dado comprimido com Zstd L19 tem entropia de **7.964 bits/byte** (máximo: 8.0).
Uma segunda camada adiciona apenas overhead de header (+39 bytes medido).

O ganho real vem de operar em abstrações diferentes **antes** da compressão.

### Por que LZMA2 como padrão e não Zstd?

| | Zstd L19 | LZMA2 extreme |
|---|---|---|
| Ratio | 4.32× | 4.48× (+3.7%) |
| Decomp speed | 562 MB/s | ~80 MB/s |
| RFC | RFC 8878 | Spec pública |
| Freeze speed | ~2 MB/s | ~3 MB/s |

Para cold data, decompressão lenta é aceitável.
LZMA2 entrega mais ratio sem custo de freeze significativo.
Zstd fica como opção para warm tier (decomp frequente).

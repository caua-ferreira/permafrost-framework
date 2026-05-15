# Especificação do Formato .permafrost

A especificação formal está em [`docs/FORMAT_SPEC.md`](https://github.com/caua-ferreira/permafrost-framework/blob/main/docs/FORMAT_SPEC.md).

## Visão geral do layout

```
[MAGIC: "PRMS" 4B]             ← identificação
[VERSION: major.minor 2B]      ← compatibilidade
[FLAGS: bitmask 2B]            ← delta|quantize|chunked|predictor|index
[CODEC_ID: 1B]                 ← 0x01=Zstd | 0x02=LZMA2
[QUANT: 1B]                    ← 0=lossless | 2=medium
[N_CHUNKS: 2B]
[SCHEMA ARROW: var]            ← schema completo embutido
[PREDICTOR MANIFEST: JSON]     ← preditor e metadados por coluna
[COMMENT: var]
[FREEZE_TIMESTAMP: int64 8B]
[ORIGINAL_ROWS: uint64 8B]
[HEADER SHA-256: 32B]          ← integridade do header

[CHUNK_0: u32_len + data + sha256_32B]
[CHUNK_1: u32_len + data + sha256_32B]
...
[CHUNK_N: u32_len + data + sha256_32B]

[SPARSE INDEX: JSON]           ← byte_offset de cada chunk
[INDEX_LEN: u32 4B]
[INDEX_SHA256: 32B]            ← integridade do índice
[EOF MAGIC: "SMRP" 4B]        ← PRMS invertido
```

## Versão atual: 1.2

| Versão | Mudança |
|--------|---------|
| 1.0 | Formato inicial: header, payload, SHA-256 |
| 1.1 | Sparse Index + chunks independentes (FLAG_CHUNKED, FLAG_INDEX) |
| 1.2 | Bug fix: preditor consistente entre chunks (manifesto fixado no freeze) |

## Garantias de compatibilidade

- Arquivos `.permafrost` v1.x são legíveis por qualquer implementação v1.y (y ≥ x)
- O schema Arrow embutido garante reconstrução sem metadados externos
- O campo `version` permite detectar versões incompatíveis
- Magic bytes `PRMS` + `SMRP` permitem identificação mesmo sem extensão de arquivo

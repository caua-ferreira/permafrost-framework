# Changelog

## [0.3.0] — 2026-05
### Adicionado
- Chunk Mode: `freeze_stream()`, `freeze_file()`, `thaw_iter()` — datasets > RAM
- SchemaDetector: suporte a JSONL, MongoDB, DynamoDB, redes sociais
- CLI completa: `permafrost freeze/thaw/audit/verify/catalog`
- Sparse Index: thaw seletivo por partição sem descomprimir o arquivo todo

## [0.2.0] — 2026-05
### Adicionado
- PermafrostCatalog (DuckDB): índice central de arquivos .permafrost
- Sparse Index embutido no formato .permafrost
- `audit()`: lê metadados sem descomprimir

## [0.1.0] — 2026-05
### Adicionado
- Formato `.permafrost` v1.0: magic PRMS, SHA-256 duplo, schema Arrow
- 5 preditores colunares: delta_zigzag, lag1_zigzag, ts_delta_s, category_u8, raw_text
- Codecs: Zstd L19, LZMA2 extreme
- Vault mode: quantização adaptativa (semi-lossy)
- Bit-rot detection em header e payload

## [0.5.0] — 2026-05
### Adicionado
- StorageAdapter: LocalAdapter, S3Adapter, GCSAdapter, AzureAdapter
- freeze_to(), thaw_from(), audit_remote() — cloud em 1 linha
- S3 range requests — audit sem download total
- PermafrostCluster: Master + Worker + Client via FastAPI
- Scheduling round-robin, retry automático (3×), healthcheck
- Múltiplos jobs paralelos, cancelamento, progress polling

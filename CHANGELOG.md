# Changelog

## [0.7.0] — 2026-05-15

### Adicionado

#### I5 — Preditor `json_schema_v2`
- Novo preditor `PRED_JSON_V2 = 'json_schema_v2'` para colunas com valores JSON
- Detecção automática: colunas com ≥70% de valores que são JSON dicts são detectadas e codificadas automaticamente
- Codificação por dicionário de chaves: chaves repetidas substituídas por índices inteiros (`key_dict` no manifesto), reduzindo bytes pré-compressão
- Fallback gracioso para chaves desconhecidas em chunks posteriores
- Compatível com Python 3.13 `str` dtype e pandas `object` dtype

#### I3 — CLI Binário Standalone (zero deps)
- `permafrost.spec`: spec PyInstaller para binário único `permafrost` / `permafrost.exe`
- `.github/workflows/release.yml`: build matrix linux/macos/windows em cada tag `v*`, smoke test + SHA-256 checksum, publica GitHub Release automaticamente
- `scripts/install.sh`: instalador Unix via `curl | sh`, verifica SHA-256
- `scripts/install.ps1`: instalador Windows PowerShell, adiciona ao PATH do usuário

#### I4 — RBAC Básico no Cluster
- JWT HS256 puro stdlib sem dependências externas (`permafrost.rbac`)
- Claims: `sub`, `can_freeze`, `can_thaw`, `namespace`, `iat`, `exp`
- `RBACManager`: gerencia usuários, valida tokens, verifica chave admin
- `PermafrostMaster` aceita `secret_key=` para habilitar RBAC; backward-compatible (default off)
- Endpoints protegidos: `POST /jobs` (freeze), `GET /jobs` (thaw), `DELETE /jobs/{id}` (freeze), `GET /workers` (thaw)
- Admin endpoints: `POST/GET/DELETE /admin/users` via header `X-Admin-Key`
- CLI: `permafrost cluster add-user / list-users / remove-user`

#### I2 — Auto Codec Selector
- `freeze(df, path, codec="auto")` analisa amostra de 1.000 linhas e escolhe codec + quantização ótimos
- `DataProfile`: perfil estatístico (float_col_ratio, int_col_ratio, str_cardinality_mean, float_cv_mean, estimated_mb)
- `auto_select()`: sistema de scoring — LZMA2 para dados estruturados estáveis, ZSTD para dados com alta variância
- `CODEC_AUTO`, `DataProfile`, `auto_select`, `profile_dataframe` exportados
- `freeze()` retorna `auto_reason` quando `codec="auto"`

#### C4 — Resumable Upload no StorageAdapter
- `upload_resumable()` em todos os adapters: retoma uploads interrompidos sem reiniciar do zero
- State file (`.upload_state` JSON) indexado por `src_mtime + src_size + remote_uri`
- Retry com backoff exponencial (3 tentativas, até 60s de espera)
- `LocalAdapter`: escrita incremental com rastreamento de `bytes_written`
- `S3Adapter`: S3 Multipart Upload com ETags persistidos no state file
- `ResumableUploadError` exportada

#### I1 — Helm Chart + Operador Kubernetes
- `charts/permafrost/`: Helm chart completo (master, worker, operator, HPA, RBAC, PVC)
- CRD `PermafrostJob` (`permafrost.io/v1alpha1`): spec com `sourcePath`, `codec`, `quant`, `partitionBy`, `chunkRows`, `masterUrl`, `token`; status com `phase`, `jobId`, `ratio`, `storedMb`
- Fases: `Pending → Running → Completed | Failed`
- Operator (`src/permafrost/operator.py`, kopf): `on_create` submete ao master, timer polling a cada 15s, `on_delete` cancela jobs ativos
- `Dockerfile.operator`: imagem kopf + permafrost
- `[kubernetes]` extra: `kopf>=1.36.0, kubernetes>=28.0.0`

---

## [0.6.4] — 2026-05
### Corrigido
- `ts_delta_s` com timestamps não ordenados (LIM-001)
- `PermafrostCatalog` thread-safe com `threading.Lock` (LIM-006)
- Compatibilidade Windows (`tmp_dir="/tmp"` → `tempfile.mkdtemp()`)

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

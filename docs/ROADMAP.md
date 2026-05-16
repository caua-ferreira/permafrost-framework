# Roadmap — Permafrost Data Framework

> **Versão atual:** 1.0.0 — branch `main`  
> **Última atualização:** 2026-05-15

---

## Estado Atual (v0.6.x — done)

Tudo abaixo está implementado, testado e publicado no PyPI.

| Componente | Status | Detalhe |
|---|---|---|
| Formato `.permafrost` | ✅ | Magic bytes, SHA-256 por chunk, schema Arrow embutido, footer sparse index |
| Preditores colunares | ✅ | delta_zigzag, lag1_zigzag, ts_delta_s, category_u8, raw_text |
| Codecs | ✅ | Zstd L19, LZMA2 extreme, ZPAQ basic |
| Quant levels | ✅ | NONE, HIGH, MEDIUM, LOW |
| `freeze()` / `thaw()` / `audit()` | ✅ | API pública estável |
| Bit-rot detection | ✅ | SHA-256 verificado antes de qualquer decompress |
| Sparse index + partial thaw | ✅ | `thaw(path, filter={col: val})` lê só chunks relevantes |
| Chunk mode (streaming) | ✅ | `freeze_file()`, `freeze_stream()`, `thaw_iter()` |
| PermafrostCatalog (DuckDB) | ✅ | Thread-safe, search por tags/período/schema |
| StorageAdapters | ✅ | S3, GCS, Azure, Local — `freeze_to()` / `thaw_from()` |
| Cluster (FastAPI) | ✅ | PermafrostMaster + PermafrostWorker + PermafrostClient |
| Spark DataSource API v2 | ✅ | Plugin opcional (requer pyspark) |
| CLI (`permafrost freeze/thaw/audit`) | ✅ | Typer + Rich |
| Suite de testes | ✅ | 385 passed, 5 skipped — CI GitHub 3.10/3.11/3.12 |
| Notebooks de exemplo | ✅ | 7 notebooks com outputs reais no repositório |
| Dockerfiles | ✅ | Dockerfile.master + Dockerfile.worker |
| PyPI | ✅ | `pip install permafrost-framework` |
| Docker Hub | ✅ | CI publica em tags v* (secrets configurados) |

---

## Crítico — v0.7 ✅ (completo)

Todas as features críticas estão implementadas, testadas e publicadas no PyPI.

### C1 — Encryption at Rest (AES-256-GCM) ✅

- [x] Chave por arquivo: `freeze(df, path, key=b"32-bytes...")` ou via env `PERMAFROST_KEY`
- [x] Cifra por chunk, não por arquivo inteiro (permite partial thaw cifrado)
- [x] KMS adapter interface: `LocalKeyProvider`, `AWSKMSProvider`, `GCPKMSProvider`
  - Envelope encryption: EDK gerado/criptografado pelo KMS; armazenado no header do arquivo
  - `thaw()` injeta o EDK automaticamente no provider para decriptografar sem intervenção do usuário
- [x] `audit()` mostra se arquivo é cifrado + qual KMS foi usado + `edek_size`
- [x] Formato: nonce (12 bytes) + tag (16 bytes) por chunk; EDK no `enc_meta` do header
- [x] Testes: round-trip cifrado, tamper detection (SHA-256 + GCM tag), mocks AWS/GCP (69 testes)

### C2 — Preditor `float32_quantized` (lossy, alta performance) ✅

- [x] Quantiza float64 → float32 (`PRED_FLOAT32`) ou float16 (`PRED_FLOAT16`)
- [x] `precision_bits` (16, 32) documentado por coluna no manifesto
- [x] `max_abs_error` e `max_rel_error` por coluna expostos em `audit()` → `lossy_columns`
- [x] Integrado com `QUANT_HIGH` (→ float32) e `QUANT_LOW` (→ float16) automaticamente
- [x] Suporte a override explícito via `freeze(df, path, predictors={"col": "float32_quantized"})`

### C3 — Schema Evolution (thaw com schema mais novo que o arquivo) ✅

- [x] `thaw()` aceita `schema_override: pa.Schema` para cast automático
- [x] Regras: colunas novas → null, colunas removidas → ignoradas, tipo compatível → cast
- [x] `schema_diff()` retorna diff entre schema gravado e schema fornecido
- [x] `apply_schema_evolution()` exportada para uso standalone
- [x] Testes de round-trip com schema evoluído em `test_schema_evolution.py`

### C4 — Retry + Resumable Upload no StorageAdapter ✅

- [x] `upload_resumable()` em LocalAdapter e S3Adapter
- [x] State file `.upload_state` JSON indexado por `src_mtime + src_size + remote_uri`
- [x] Retry com exponential backoff (3 tentativas, até 60s)
- [x] S3 Multipart Upload com ETags persistidos; `ResumableUploadError` exportada

---

## Importante — v0.8 ✅ (completo)

Todas as features de ecossistema estão implementadas.

### I1 — Helm Chart + Kubernetes Operator ✅

- [x] `charts/permafrost/` — Helm chart com values.yaml configurável (replicas, resources, storageClass, HPA, RBAC)
- [x] CRD `PermafrostJob` (apiVersion: permafrost.io/v1alpha1) — spec com sourcePath, codec, quant, partitionBy, chunkRows, masterUrl, token; status com phase, jobId, ratio, storedMb
- [x] Operator (`src/permafrost/operator.py`, kopf) — on_create submete ao master, monitor timer polling a cada 15s, on_delete cancela jobs running
- [x] `Dockerfile.operator` — imagem kopf + permafrost
- [x] Fases: Pending → Running → Completed | Failed

### I2 — Codec Auto-Selector (ML leve) ✅

- [x] Analisa amostra de 1.000 linhas e escolhe codec + quantização ótimos (`auto_select()`)
- [x] `DataProfile`: float_col_ratio, str_cardinality_mean, float_cv_mean, estimated_mb
- [x] API: `freeze(df, path, codec="auto")` — retorna `auto_reason` nas métricas
- [x] `CODEC_AUTO`, `DataProfile`, `auto_select`, `profile_dataframe` exportados

### I3 — CLI Binary Standalone (zero-deps) ✅

- [x] Compilar com PyInstaller: binário único `permafrost.exe` / `permafrost` (`permafrost.spec`)
- [x] Funciona sem Python instalado — garantia de leitura em 2040
- [x] Publish: GitHub Releases via `.github/workflows/release.yml` + instalar via `curl | sh`
- [x] Install scripts: `scripts/install.sh` (Unix) e `scripts/install.ps1` (Windows)
- [x] Smoke tests + SHA-256 verificado no CI antes de publicar

### I4 — RBAC Básico no Cluster ✅

- [x] JWT simples: token com claims `can_freeze`, `can_thaw`, `namespace`
- [x] Master valida token em todos os endpoints
- [x] `permafrost cluster add-user <name> --can-freeze --namespace prod`
- [x] Sem dependências externas (sem Keycloak, sem LDAP na v0.8)

### I5 — Preditor `json_schema_v2` (NoSQL melhorado) ✅

- [x] Detecta colunas com ≥70% de valores JSON dicts automaticamente
- [x] Compressão de chaves JSON por dicionário compartilhado por chunk (`key_dict`)
- [x] Reduz bytes pré-compressão para schemas com chaves longas
- [x] Compatível com Python 3.13 `str` dtype e legado `object` dtype

---

## v1.0 ✅ — Production Ready (completo)

### v1.0 — Production Ready

- [x] Spec formal `.permafrost` publicada como RFC draft (`docs/format-spec.md`) ✅
- [x] `PermafrostContext` — API de alto nível unificando catalog + storage + cluster ✅
  - `ctx.freeze(df, name)` — freeze + upload + catalog register em uma chamada
  - `ctx.thaw(name, filter=...)` — download + thaw
  - `ctx.audit(name)` — range-request audit remoto
  - `ctx.search(...)`, `ctx.cost_report()`, `ctx.stats()`, `ctx.sql()` — delegados ao catalog
  - `ctx.freeze_async()` + `ctx.wait()` — integração com cluster distribuído
  - Context manager (`with PermafrostContext(...) as ctx`)
- [x] Documentação completa (MkDocs Material, Getting Started, API Reference) ✅
- [x] Python SDK estável com semantic versioning + deprecation policy ✅

### v2.0 — Intelligence

- [ ] Cross-dataset solid compression (dicionário global treinado no corpus)
- [ ] Compression-aware query pushdown (pushdown de filtros até o codec)
- [ ] Permafrost Hub: registry de plugins (codecs, adapters, predictors)
- [ ] Suporte a streaming em tempo real (Kafka → freeze incremental)

### v3.0 — Universal Archive

- [ ] Format bridge: importar tar, ZIP, WARC, ORC, Avro
- [ ] Full-text search dentro de dados congelados (sem thaw completo)
- [ ] Edge compression nodes: freeze próximo à fonte (IoT, edge computing)
- [ ] SDK para outras linguagens: Rust (core), Go (CLI), Java (Spark nativo)

---

## Platform (produto comercial — roadmap separado)

O Permafrost segue o modelo **open-core**: o framework permanece open-source e gratuito. Uma plataforma de gestão visual será desenvolvida como produto comercial separado sobre este core.

**Proposta:** interface web para gestão de arquivos `.permafrost` em escala empresarial, com integração nativa a contas AWS / GCP / Azure do próprio cliente — os dados nunca saem da infraestrutura do cliente.

Detalhes no repositório privado do produto.

---

## Analogia com Apache Spark

| Spark | Permafrost | Status |
|---|---|---|
| SparkContext | PermafrostContext | v1.0 |
| RDD / DataFrame | `.permafrost` Dataset | ✅ |
| Spark Master | PermafrostMaster | ✅ |
| Spark Executor | PermafrostWorker | ✅ |
| Catalyst Optimizer | Codec Auto-Selector | v0.8 |
| Spark SQL | Thaw Query + filtros | ✅ parcial |
| DataSource API v2 | StorageAdapter Plugin API | ✅ |
| spark-submit | `permafrost freeze/thaw` CLI | ✅ |
| Spark Security | Encryption + RBAC | v0.7 / v0.8 |
| Kubernetes Operator | Helm Chart + CRD | v0.8 |

---

## Decisões de design fixas (não mudar)

- **Formato self-contained:** cada `.permafrost` deve ser legível sem catálogo externo
- **Bit-rot first:** SHA-256 verificado ANTES de qualquer I/O de decompress
- **Chunk independência:** cada chunk é um arquivo válido — falha em um não quebra os outros
- **Zero lock-in de cloud:** StorageAdapter é interface — trocar S3 por GCS não muda código do usuário
- **Leitura em 2040:** CLI binary standalone é requisito de v1.0, não opcional

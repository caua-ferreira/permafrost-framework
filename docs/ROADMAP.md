# Roadmap — Permafrost Data Framework

> **Versão atual:** 0.6.4 — branch `main`  
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

## Crítico — v0.7 (próximo sprint)

Features que bloqueiam adoção em produção. **Começar por aqui.**

### C1 — Encryption at Rest (AES-256-GCM) ✅

- [x] Chave por arquivo: `freeze(df, path, key=b"32-bytes...")` ou via env `PERMAFROST_KEY`
- [x] Cifra por chunk, não por arquivo inteiro (permite partial thaw cifrado)
- [x] KMS adapter interface: `LocalKeyProvider`, `AWSKMSProvider`, `GCPKMSProvider`
  - Envelope encryption: EDK gerado/criptografado pelo KMS; armazenado no header do arquivo
  - `thaw()` injeta o EDK automaticamente no provider para decriptografar sem intervenção do usuário
- [x] `audit()` mostra se arquivo é cifrado + qual KMS foi usado + `edek_size`
- [x] Formato: nonce (12 bytes) + tag (16 bytes) por chunk; EDK no `enc_meta` do header
- [x] Testes: round-trip cifrado, tamper detection (SHA-256 + GCM tag), mocks AWS/GCP (69 testes)

### C2 — Preditor `float32_quantized` (lossy, alta performance)

**Por que é crítico:** sensores, embeddings ML, séries temporais financeiras — maior ganho de compressão com perda controlada.

- [ ] Quantiza float64 → float32 (ou int16 com scale factor)
- [ ] Parâmetro `precision_bits` (16, 32) por coluna ou global
- [ ] Erro máximo garantido: documentado no `audit()` por coluna
- [ ] Integração com `QUANT_HIGH` / `QUANT_MEDIUM` já existentes
- [ ] Benchmark: comparar com Parquet ZSTD em dataset de embeddings 1536-dim

### C3 — Schema Evolution (thaw com schema mais novo que o arquivo)

**Por que é crítico:** dados congelados em 2024 precisam ser lidos em 2030 com schema diferente.

- [ ] `thaw()` aceita `schema_override: pa.Schema` para cast automático
- [ ] Regras: colunas novas → null, colunas removidas → ignoradas, tipo compatível → cast
- [ ] `audit()` mostra diff entre schema gravado e schema atual do catalog
- [ ] Testes: arquivo v0.6 lido por código v1.0 com schema evoluído

### C4 — Retry + Resumable Upload no StorageAdapter

**Por que é crítico:** uploads de arquivos grandes para S3/GCS falham em links instáveis. Hoje um erro no meio força reiniciar do zero.

- [ ] Interface: `upload_resumable(local_path, remote_uri, chunk_size=100MB)`
- [ ] State file local (`.permafrost.upload_state`) para retomar
- [ ] Retry com exponential backoff (3 tentativas, max 60s)
- [ ] Suporte: S3 multipart upload, GCS resumable upload, Azure block blob

---

## Importante — v0.8

Qualidade de vida e ecossistema. Fazer depois do v0.7.

### I1 — Helm Chart + Kubernetes Operator ✅

- [x] `charts/permafrost/` — Helm chart com values.yaml configurável (replicas, resources, storageClass, HPA, RBAC)
- [x] CRD `PermafrostJob` (apiVersion: permafrost.io/v1alpha1) — spec com sourcePath, codec, quant, partitionBy, chunkRows, masterUrl, token; status com phase, jobId, ratio, storedMb
- [x] Operator (`src/permafrost/operator.py`, kopf) — on_create submete ao master, monitor timer polling a cada 15s, on_delete cancela jobs running
- [x] `Dockerfile.operator` — imagem kopf + permafrost
- [x] Fases: Pending → Running → Completed | Failed

### I2 — Codec Auto-Selector (ML leve)

- [ ] Analisa amostra de 1000 linhas do DataFrame e escolhe codec + nível ótimo
- [ ] Modelo: decision tree treinada em benchmarks internos (sem dependência externa)
- [ ] API: `freeze(df, path, codec="auto")` — padrão futuro
- [ ] Benchmark: auto-selector bate seleção manual em >80% dos datasets de teste

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

## Futuro — v1.0+ (quando I-series estiver done)

### v1.0 — Production Ready

- [ ] Spec formal `.permafrost` publicada como RFC draft no GitHub
- [ ] Certificação de compatibilidade para storage vendors
- [ ] `PermafrostContext` — API de alto nível unificando catalog + storage + cluster
- [ ] Documentação completa (MkDocs Material, Getting Started em 15 min)
- [ ] Python SDK estável com semantic versioning + deprecation policy

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

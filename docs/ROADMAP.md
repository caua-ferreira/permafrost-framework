# Roadmap — Permafrost Data Framework

## Visão de 3 Anos

O Permafrost segue a mesma estratégia que tornou o Apache Spark sustentável:
documentação de qualidade, governança transparente, ecossistema de plugins e comunidade ativa.

---

## v0.1 — Prototype (✅ Atual)

**Entregues:**
- [x] Formato `.permafrost` com magic bytes, SHA-256 duplo, schema Arrow embutido
- [x] 5 preditores colunares: delta_zigzag, lag1_zigzag, ts_delta_s, category_u8, raw_text
- [x] Codecs: Zstd L19 + LZMA2 extreme
- [x] Quant levels: lossless, high, medium
- [x] `freeze()` + `thaw()` + `audit()` funcionando em Python
- [x] Bit-rot detection antes de qualquer decompressão
- [x] Benchmarks completos: 80k linhas × 18 colunas, projeção 10 GB
- [x] Estudo completo de algoritmos (Zstd, LZMA2, Brotli, ZPAQ, Parquet)

---

## v0.2 — Sparse Index

**Meta:** thaw parcial sem descomprimir o arquivo inteiro

- [ ] Índice esparso no final do arquivo (inspirado no Parquet footer)
- [ ] Estrutura: `[n_entries:u32][partition_key][row_start:u64][byte_offset:u64]...`
- [ ] `thaw(path, filter={'ano': 2022, 'regiao': 'Sul'})` — lê só os chunks necessários
- [ ] `footer_offset` nos últimos 8 bytes — leitura de trás para frente
- [ ] Testes: verificar que thaw de 1% dos dados não descomprime mais de 5% do arquivo

---

## v0.3 — Chunk Mode + Catalog

**Meta:** suporte a datasets grandes e catálogo de busca

- [ ] Divisão automática em chunks de 256 MB (configurável)
- [ ] Cada chunk é um `.permafrost` independente com seu próprio SHA-256
- [ ] PDS Manifest: arquivo JSON listando todos os chunks de um dataset
- [ ] PermafrostCatalog (DuckDB local):
  - Indexa todos os `.permafrost` por localização, schema, período, custo
  - API: `catalog.search(schema_contains='preco', period_start='2020-01-01')`
  - Estimativa de custo de storage por dataset

---

## v0.4 — Cluster Básico

**Meta:** pipeline distribuído rodando em múltiplos workers

- [ ] PermafrostMaster: REST API (FastAPI), recebe jobs, faz scheduling
- [ ] PermafrostWorker: executa pipeline L0→L4 em paralelo por chunk
- [ ] Job checkpointing: retoma de onde parou em caso de falha
- [ ] Docker Compose: cluster local com 1 master + 3 workers
- [ ] Métricas básicas: throughput, ratio médio, erros

---

## v0.5 — StorageAdapters

**Meta:** escrever e ler de cloud storage

- [ ] StorageAdapter interface: `upload(local_path, remote_uri)`, `download(remote_uri, local_path)`
- [ ] S3Adapter: upload multipart, presigned URLs
- [ ] GCSAdapter: Google Cloud Storage
- [ ] AzureAdapter: Azure Blob Storage
- [ ] HDFSAdapter: para ambientes on-premise

---

## v1.0 — Production Ready

**Meta:** pronto para uso em produção em empresas

- [ ] Kubernetes Operator + Helm Chart v1.0
- [ ] Apache Spark DataSource API v2 plugin
- [ ] ZPAQ codec (method=5) para Vault tier
- [ ] RBAC básico: quem pode freeze, quem pode thaw
- [ ] Documentação completa (Getting Started em 15 minutos)
- [ ] Spec formal do formato `.permafrost` publicada como RFC draft
- [ ] CLI standalone (binary zero-deps) — leitor de `.permafrost` para 2040

---

## v2.0 — Intelligence

**Meta:** auto-seleção de codec e otimizações avançadas

- [ ] Codec auto-selector: analisa amostra do dado e escolhe codec + nível ótimos via ML
- [ ] Cross-dataset solid compression com dicionário global treinado no corpus
- [ ] Compression-aware query pushdown
- [ ] Permafrost Hub: registry de plugins (codecs, adapters, predictors)

---

## v3.0 — Universal Archive

**Meta:** interoperabilidade universal

- [ ] Format bridge: importar dados de tar, ZIP, WARC, ORC
- [ ] API de busca full-text dentro de dados congelados (sem thaw completo)
- [ ] Edge compression nodes: nós leves para freeze próximo à fonte de dados
- [ ] Certification Program: vendors de storage podem obter certificação de compatibilidade

---

## Analogia com Apache Spark

| Spark | Permafrost |
|---|---|
| SparkContext | PermafrostContext |
| RDD / DataFrame | Permafrost Dataset (.permafrost) |
| Spark Master | PermafrostMaster |
| Spark Executor | PermafrostWorker |
| Catalyst Optimizer | Codec Auto-Selector (v2.0) |
| Spark SQL | Thaw Query API + filtros |
| DataSource API v2 | StorageAdapter Plugin API |
| spark-submit | `permafrost freeze / thaw` CLI |

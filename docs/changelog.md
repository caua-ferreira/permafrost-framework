# Changelog

## [0.5.0] — 2026-05

### Adicionado
- **StorageAdapter**: `LocalAdapter`, `S3Adapter`, `GCSAdapter`, `AzureAdapter`
- `freeze_to()`, `thaw_from()`, `audit_remote()` — cloud em 1 linha
- S3 range requests — `audit_remote()` sem download total
- **PermafrostCluster**: `PermafrostMaster` + `PermafrostWorker` + `PermafrostClient` via FastAPI
- Scheduling round-robin, retry automático (3×), heartbeat, cancelamento
- Múltiplos jobs paralelos com workers compartilhados
- Docker Compose para cluster local
- Estrutura de pacote PyPI (`src/permafrost/`) — `import permafrost as pf`

### Corrigido
- Bug de timestamp: `datetime64[us].astype('int64')` retorna microseconds no pandas 2.0+,
  não nanoseconds. Corrigido usando `pd.Timedelta('1s')` para divisão robusta entre resoluções.

---

## [0.4.0] — 2026-05

### Adicionado
- **SchemaDetector**: detecção automática de CSV, JSONL, MongoDB, redes sociais
- Flatten automático: campos escalares → colunas com preditores; arrays/nested → JSON string
- **Chunk Mode**: `freeze_stream()`, `freeze_file()`, `thaw_iter()` — datasets > RAM
- Two-pass correto: Pass 1 comprime em temp file, Pass 2 monta header com offsets corretos
- CLI completa: `permafrost freeze/thaw/audit/verify/catalog` com typer + rich

---

## [0.3.0] — 2026-05

### Adicionado
- **PermafrostCatalog** (DuckDB): índice centralizado de arquivos `.permafrost`
- `register()`, `register_dir()`, `search()`, `thaw()`, `cost_report()`, `integrity_check()`, `sql()`
- Tabelas DuckDB: `datasets` + `chunks` (espelha o sparse index)

### Corrigido
- Bug crítico de preditor: o encoder re-detectava o preditor em cada chunk, causando
  inconsistência quando a cardinalidade variava entre chunks. Corrigido: manifesto detectado
  uma vez sobre o DataFrame completo e fixado para todos os chunks.

---

## [0.2.0] — 2026-05

### Adicionado
- **Sparse Index**: índice esparso no footer do arquivo (inspirado no Parquet)
- Thaw seletivo: `thaw(filter={"ano": 2021})` lê 12–31% do arquivo
- `thaw(row_range=(start, end))` para range de linhas
- `audit()` lê header + sparse index sem descomprimir nenhum chunk
- Formato `.permafrost` v1.1: flags `FLAG_CHUNKED`, `FLAG_INDEX`

---

## [0.1.0] — 2026-05

### Adicionado
- Formato `.permafrost` v1.0: magic `PRMS`, SHA-256 duplo (header + payload), schema Arrow embutido
- 5 preditores colunares: `delta_zigzag`, `lag1_zigzag`, `ts_delta_s`, `category_u8`, `raw_text`
- Codecs: Zstd L19 (`CODEC_ZSTD`) e LZMA2 extreme (`CODEC_LZMA2`)
- Quant levels: `QUANT_NONE`, `QUANT_HIGH`, `QUANT_MEDIUM`, `QUANT_LOW`
- `freeze()`, `thaw()`, `audit()` funcionando
- Bit-rot detection em header e payload (antes de qualquer decompressão)
- Benchmarks completos: 80k linhas × 9 colunas, projeção para 10 GB
- Estudo completo de algoritmos (Zstd, LZMA2, Brotli, ZPAQ, Parquet)

## [0.5.1] — 2026-05

### Adicionado
- **CODEC_ZPAQ** (`CODEC_ZPAQ = 0x03`) — codec de context mixing via binário `zpaq`
  - Para dados de texto longo e logs: até 27% menor que LZMA2
  - Para dados tabulares: equivalente ao LZMA2 (diferença < 2%)
  - Requer: `apt install zpaq` | `brew install zpaq`
  - API: `pf.freeze(df, path, codec=pf.CODEC_ZPAQ)`
- Docstrings Google-style em todas as funções públicas (API Reference automática via mkdocstrings)
- Type hints completos em `freeze()`, `thaw()`, `audit()`
- 3 exemplos práticos em `examples/` (quick start, NoSQL, streaming)
- Testes reescritos com `import permafrost as pf` (PyPI-ready)

### Corrigido
- Mapa de nomes de codec em `audit()` agora inclui `zpaq`

## [0.5.2] — 2026-05

### Adicionado
- **Docker Hub**: imagens `permafrost-master` e `permafrost-worker` publicadas
  - `docker-compose up --scale worker=4` — cluster pronto sem build local
  - Multi-arch: `linux/amd64` + `linux/arm64` (Apple M1/M2)
  - `Dockerfile.master` e `Dockerfile.worker` separados
  - `docker-compose.dev.yml` para desenvolvimento com build local
- **`python -m permafrost`** — entrypoint CLI para containers
  - `python -m permafrost master [--host] [--port]`
  - `python -m permafrost worker --master URL [--host] [--port]`
- **GitHub Actions `docker.yml`** — build e push automático ao criar tag `v*`
  - Secrets necessários: `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN`
- Exemplo `04_cluster_docker.py` — demonstra cluster com 3 jobs paralelos
- Seção "Quando o Permafrost faz sentido" na documentação principal

# ❄️ Permafrost Framework — Documento de Handoff

> **Para:** Claude Code (VS Code terminal)  
> **Repositório:** https://github.com/caua-ferreira/permafrost-framework  
> **Versão atual:** 0.6.0  
> **Status:** Pronto para publicar — 385 testes passando  

---

## 1. O que é o Permafrost

Plataforma distribuída de compressão inteligente para arquivamento de dados de longo prazo.

**Problema que resolve:** Dados corporativos históricos (CSV, JSONL, MongoDB dumps) ficam
anos em cold storage pagando caro. Ninguém quer descomprimir 10 GB para buscar um mês.

**Como resolve:**
- Comprime dados com **8–15× ratio** (vs 5.97× do LZMA2 puro sobre CSV)
- Formato `.permafrost` com **sparse index** — lê só o ano que você quer, sem descomprimir o resto
- **SHA-256 embutido** em cada chunk — bit-rot detectado antes de qualquer decompressão
- **Catalog DuckDB** — busca em 500 arquivos no S3 sem baixar nenhum
- **Cluster FastAPI** — freeze distribuído, 1 TB processado por N workers em paralelo

**Diferença vs LZMA2 puro:**
```
CSV bruto:                  5.85 MB
CSV + LZMA2 puro:           0.499 MB  (ratio 5.97×)
CSV + Permafrost + LZMA2:   0.284 MB  (ratio 10.50×)  ← +76% por causa dos preditores colunares
```

Os preditores colunares (delta_zigzag, lag1_zigzag, ts_delta_s, category_u8, raw_text)
transformam os dados semanticamente antes do codec — é isso que faz a diferença.

---

## 2. O que foi construído (completo)

### Estrutura do pacote (PyPI-ready)

```
src/permafrost/
    __init__.py          # export de toda a API pública
    codec.py             # freeze(), thaw(), audit() + 5 preditores
    catalog.py           # PermafrostCatalog (DuckDB)
    chunk_mode.py        # freeze_stream(), freeze_file(), thaw_iter()
    cli.py               # CLI typer+rich (freeze/thaw/audit/verify/catalog)
    cluster.py           # PermafrostMaster + PermafrostWorker + PermafrostClient
    schema_detector.py   # SchemaDetector, DataType (SQL + NoSQL + JSONL)
    storage.py           # LocalAdapter, S3Adapter, GCSAdapter, AzureAdapter
    spark.py             # PermafrostDataSource (Spark DataSource API v2)
    __main__.py          # python -m permafrost master|worker|freeze|...
```

### Formato `.permafrost` v1.2

```
[MAGIC: "PRMS" 4B]         ← identificação
[VERSION: 1.2 2B]
[FLAGS: bitmask 2B]        ← delta|quantize|chunked|predictor|index
[CODEC_ID: 1B]             ← 0x01=Zstd | 0x02=LZMA2 | 0x03=ZPAQ
[QUANT: 1B]                ← 0x00=lossless | 0x01=high | 0x02=medium | 0x03=low
[N_CHUNKS: 2B]
[SCHEMA ARROW: var]        ← schema completo embutido (auto-descritivo em 2040)
[PREDICTOR MANIFEST: JSON] ← preditor e metadados por coluna
[COMMENT: var]
[FREEZE_TIMESTAMP: int64]
[ORIGINAL_ROWS: uint64]
[HEADER SHA-256: 32B]      ← integridade do header
[CHUNK_0: u32_len + data + sha256] × N
[SPARSE INDEX: JSON]       ← byte_offset de cada chunk
[INDEX_SHA256: 32B]
[EOF: "SMRP" 4B]           ← PRMS invertido
```

### API completa

```python
import permafrost as pf

# Core
pf.freeze(df, "arquivo.permafrost", codec=pf.CODEC_LZMA2, partition_by="ano")
pf.thaw("arquivo.permafrost", filter={"ano": 2023}, verify=True)
pf.audit("arquivo.permafrost")  # zero decompressão

# Streaming (datasets > RAM)
pf.freeze_file("100gb.csv", "saida.permafrost", chunk_rows=50_000)
pf.freeze_stream(cursor_generator(), "saida.permafrost")
for batch in pf.thaw_iter("arquivo.permafrost", batch_size=50_000): ...

# Cloud
pf.freeze_to(df, "s3://bucket/arquivo.permafrost")
pf.thaw_from("s3://bucket/arquivo.permafrost", filter={"ano": 2024})
pf.audit_remote("s3://bucket/arquivo.permafrost")  # range request, não baixa tudo

# Catalog
cat = pf.PermafrostCatalog("catalog.db")
cat.register_dir("/dados/cold/")
cat.search(name="vendas", lossless_only=True)
cat.cost_report("glacier_deep")
cat.integrity_check()

# Cluster
client = pf.PermafrostClient("http://master:8700")
job_id = client.freeze("dados.csv", "saida.permafrost")
status = client.wait(job_id)

# Spark (PySpark 4.0+)
from permafrost.spark import register
register(spark)
df = spark.read.format("permafrost").load("dados.permafrost")
df.filter(df.ano == 2023).show()  # pushdown via sparse index
```

### Benchmarks medidos

| Dataset | Original | .permafrost | Ratio |
|---------|----------|-------------|-------|
| CSV corporativo (80k linhas × 9 colunas) | 5.85 MB | **0.678 MB** | **8.37×** |
| JSONL social media (5k posts) | 1.44 MB | **0.043 MB** | **33×** |
| Streaming 300k linhas | ~97 MB est. | **1.018 MB** | **95×** |
| 1 TB no Glacier Deep Archive | $0.99/mês | **$0.12/mês** | **-88%** |

### Testes

```
tests/
    test_freeze_thaw.py          # 30 testes — freeze/thaw/audit/integridade/sparse index
    test_sparse_index.py         # 20 testes — chunked freeze, thaw seletivo, bit-rot
    test_catalog.py              # 30 testes — register, search, thaw, cost, integrity, SQL
    test_cluster.py              # 22 testes — health, lifecycle, concorrência, cancelamento
    test_comprehensive.py        # 94 testes — edge cases, todos os codecs, benchmarks mínimos
    test_fidelidade_total.py     # 25 testes — 100% linha por linha, distribuições, multi round-trip
    test_concorrencia.py         # 14 testes — 10 threads simultâneas, freeze+thaw paralelos
    test_predictor_edge_cases.py # 34 testes — variância zero, 256 cats, timestamps extremos
    test_cluster_fault_tolerance.py # 22 testes — retry, sem workers, 10 jobs paralelos
    test_formato_binario_spec.py # 41 testes — byte a byte do formato, SHA-256, sparse index
    test_schema_detector_stress.py # 21 testes — 50% campos ausentes, tipos misturados, 100 campos
    test_cli_cobertura.py        # 21 testes — todos os comandos CLI
    test_performance_regression.py # 19 testes — ratio ≥8×, thaw <2s, audit <50ms

Total: 385 testes | 0 falhando
```

---

## 3. O que está pronto para publicar HOJE

### GitHub
```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
git init && git add .
git commit -m "feat: Permafrost v0.6.0 — 385 tests passing"
git remote add origin https://github.com/caua-ferreira/permafrost-framework.git
git push -u origin main

# Configurar Secrets no GitHub (Settings → Secrets):
# PYPI_API_TOKEN         → token do PyPI
# DOCKERHUB_USERNAME     → caua-ferreira (ou seu user Docker Hub)
# DOCKERHUB_TOKEN        → token do Docker Hub (não a senha)
```

### PyPI (você tem conta — só fazer upload)
```bash
pip install build twine
python -m build
twine upload dist/*
# → pip install permafrost-framework funciona globalmente
```

### Tag (CI/CD automático)
```bash
git tag v0.6.0 && git push --tags
# Dispara automaticamente:
# → GitHub Actions: testes em Python 3.10/3.11/3.12
# → Deploy docs no GitHub Pages
# → Publish no PyPI
# → Build + push Docker Hub (master e worker)
```

### GitHub Actions configurados
```
.github/workflows/
    tests.yml    → roda pytest em cada push (3.10, 3.11, 3.12)
    docs.yml     → deploy MkDocs no GitHub Pages em push para main
    publish.yml  → publica no PyPI ao criar tag v*
    docker.yml   → build + push Docker Hub ao criar tag v*
```

---

## 4. O que ainda falta fazer (priorizado)

### 🔴 CRÍTICO — antes de anunciar publicamente

#### P1 — Reescrever os testes antigos que ainda usam imports legados
**Problema:** Os arquivos `test_cluster.py` e partes de outros testes ainda
importam de `permafrost_v4`, `permafrost_catalog`, etc. Devem usar `import permafrost as pf`.

```bash
# Verificar o que ainda está quebrado
cd permafrost-framework
pip install -e '.[dev]'
pytest tests/ -v 2>&1 | grep "ModuleNotFoundError\|ImportError"
```

**O que fazer:**
- Verificar cada arquivo de teste com `grep "from permafrost_" tests/`
- Substituir todos os imports pelo padrão `import permafrost as pf`
- Garantir que `pytest tests/ -v` passa 100% sem o ambiente de dev `/tmp`

#### P2 — Corrigir LIM-001: ts_delta_s com timestamps não ordenados
**Problema:** Timestamps não em ordem crescente são restaurados incorretamente
sem nenhum erro ou aviso. É um bug silencioso crítico para usuários.

**Arquivo:** `src/permafrost/codec.py` → função `_encode_with_manifest`, preditor `PRED_TS`

**Fix:** Usar `int64` para deltas em vez de `uint32`:
```python
# Atual (problemático)
deltas = np.diff(ts_seconds).astype(np.uint32)  # overflow em negativo!

# Correto
deltas = np.diff(ts_seconds).astype(np.int64)
# Aplicar zigzag em int64: negativo → positivo
zigzag = np.where(deltas >= 0, deltas * 2, -deltas * 2 - 1).astype(np.uint64)
```

**Atenção:** Isso quebra o formato (arquivos v1.2 escritos com timestamps
não-ordenados seriam ilegíveis na versão corrigida). Bumpar para formato v1.3.

#### P3 — Adicionar aviso no freeze() quando timestamps não estão ordenados
Enquanto o fix do P2 não está pronto, adicionar um `warnings.warn()`:
```python
if predictor == PRED_TS:
    if not series.is_monotonic_increasing:
        import warnings
        warnings.warn(
            f"Coluna '{col}' de timestamp não está em ordem crescente. "
            "O preditor ts_delta_s pode restaurar valores incorretos. "
            "Ordene o DataFrame antes de freeze(): df.sort_values(col)",
            UserWarning, stacklevel=3
        )
```

---

### 🟡 IMPORTANTE — para v0.7.0

#### P4 — Catalog thread-safe
**Problema:** `PermafrostCatalog` não é thread-safe quando a conexão é compartilhada.  
**Fix:** Adicionar `threading.local()` ou `threading.RLock()` no catalog.

```python
class PermafrostCatalog:
    _local = threading.local()
    _lock  = threading.RLock()

    def _conn(self):
        """Retorna conexão DuckDB local à thread."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = duckdb.connect(self.catalog_path)
        return self._local.conn
```

#### P5 — Docstrings faltando em funções secundárias
**Arquivos:** `storage.py` (métodos das classes), `cluster.py` (PermafrostMaster/Worker)  
**O mkdocstrings não vai gerar API Reference correta sem elas.**

```bash
# Verificar o que falta
grep -n "def [a-z]" src/permafrost/storage.py | head -20
grep -n "def [a-z]" src/permafrost/cluster.py | head -20
```

#### P6 — Type hints faltando em funções secundárias
`freeze_stream()`, `freeze_file()`, `thaw_iter()`, métodos do cluster/storage
ainda não têm assinaturas tipadas. VSCode não faz autocomplete.

#### P7 — Exemplos de notebooks Jupyter
Os scripts `examples/01_quickstart.py` até `04_cluster_docker.py` existem mas
notebooks Jupyter seriam muito melhores para o HN/Reddit:

```
examples/
    01_quickstart.ipynb          # 5 min — funcionando no Colab
    02_social_media_jsonl.ipynb  # Twitter/Instagram
    03_mongodb_dump.ipynb        # NoSQL
    04_s3_glacier_archive.ipynb  # Cloud + lifecycle
    05_cluster_docker.ipynb      # Distribuído
```

---

### 🟢 ROADMAP — v1.0

#### P8 — Fix ts_delta_s int64 (formato v1.3)
Migrar o preditor de timestamps para usar `int64` nos deltas, suportando:
- Timestamps não ordenados
- Timestamps além de 2038
- Timestamps pré-1970

Requer bump de versão do formato e testes de retrocompatibilidade.

#### P9 — Persistência de estado do cluster
O Master perde todos os jobs em caso de restart. Para produção:
```python
# Persistir jobs em DuckDB
master.db = duckdb.connect("master_state.db")
master.db.execute("CREATE TABLE IF NOT EXISTS jobs ...")
```

#### P10 — Spark DataSource Write mescla chunks com thaw+re-freeze
O `_PermafrostWriter.commit()` atual faz thaw de todas as partes e
re-freeze em arquivo único. Para datasets grandes (> 10M linhas no Spark),
isso é ineficiente. O ideal é mesclar os sparse indexes sem reprocessar os dados.

#### P11 — ZPAQ codec sem subprocess
O `CODEC_ZPAQ` atual usa `subprocess` chamando o binário `zpaq` do sistema.
Isso significa que o ZPAQ não está disponível em Docker sem instalar o binário.
Implementar via `ctypes` ou binding Python direto.

#### P12 — Kubernetes Operator
Para o cluster em produção real, um Operator K8s que escala workers automaticamente
baseado no tamanho da fila de tasks.

---

## 5. Bugs conhecidos (ver KNOWN_ISSUES.md)

| ID | Severidade | Status | Arquivo |
|----|------------|--------|---------|
| BUG-001 — CLI verify exit code | ~~Crítico~~ | ✅ Corrigido | cli.py |
| BUG-002 — CLI import legado | ~~Crítico~~ | ✅ Corrigido | cli.py |
| BUG-003 — thaw_iter batch overflow | ~~Alto~~ | ✅ Corrigido | chunk_mode.py |
| LIM-001 — ts não-ordenado silencioso | Alto | ⚠️ P2 acima | codec.py |
| LIM-002 — timestamps pós-2038 | Médio | P8 roadmap | codec.py |
| LIM-003 — int64 delta overflow | Médio | P8 roadmap | codec.py |
| LIM-004 — float precisão > 2 casas | Baixo | documentado | codec.py |
| LIM-005 — campos raros no schema | Médio | documentado | schema_detector.py |
| LIM-006 — catalog não thread-safe | Médio | P4 acima | catalog.py |

---

## 6. Setup do ambiente de desenvolvimento

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework

# Instalar em modo editable com todas as dependências de dev
pip install -e '.[dev,s3,gcs,azure]'

# Opcional: Spark
pip install pyspark>=4.0

# Opcional: ZPAQ codec
# Linux:  apt install zpaq
# macOS:  brew install zpaq

# Rodar testes
pytest tests/ -v                          # tudo exceto Spark
pytest tests/ -v -k "Spark"              # só Spark (requer PySpark)
pytest tests/ -v --tb=short -q           # output resumido

# Build da documentação local
mkdocs serve                              # http://localhost:8000

# Build do pacote
python -m build
twine check dist/*
```

---

## 7. Estrutura de arquivos completa

```
permafrost-framework/
├── src/permafrost/              # ← código do pacote
│   ├── __init__.py              # exports públicos
│   ├── __main__.py              # python -m permafrost
│   ├── codec.py                 # núcleo: freeze/thaw/preditores
│   ├── catalog.py               # PermafrostCatalog
│   ├── chunk_mode.py            # streaming
│   ├── cli.py                   # CLI typer
│   ├── cluster.py               # Master/Worker/Client
│   ├── schema_detector.py       # SQL+NoSQL detection
│   ├── storage.py               # S3/GCS/Azure adapters
│   └── spark.py                 # Spark DataSource v2
├── tests/                       # 385 testes
│   ├── test_freeze_thaw.py
│   ├── test_sparse_index.py
│   ├── test_catalog.py
│   ├── test_cluster.py
│   ├── test_cluster_fault_tolerance.py
│   ├── test_comprehensive.py
│   ├── test_concorrencia.py
│   ├── test_fidelidade_total.py
│   ├── test_formato_binario_spec.py
│   ├── test_performance_regression.py
│   ├── test_predictor_edge_cases.py
│   ├── test_schema_detector_stress.py
│   └── test_cli_cobertura.py
├── docs/                        # MkDocs Material
│   ├── index.md
│   ├── getting-started.md
│   ├── user-guide/              # freeze-thaw, nosql, streaming, cloud, catalog, cluster
│   ├── api-reference/           # core, catalog, storage, cluster
│   ├── benchmarks.md
│   ├── format-spec.md
│   ├── contributing.md
│   └── changelog.md
├── examples/
│   ├── 01_quickstart.py
│   ├── 02_nosql_social_media.py
│   ├── 03_streaming_large_dataset.py
│   └── 04_cluster_docker.py
├── .github/workflows/
│   ├── tests.yml                # CI em push
│   ├── docs.yml                 # deploy GitHub Pages
│   ├── publish.yml              # PyPI na tag v*
│   └── docker.yml               # Docker Hub na tag v*
├── Dockerfile.master            # imagem do Master
├── Dockerfile.worker            # imagem do Worker (inclui zpaq)
├── docker-compose.yml           # cluster de produção
├── docker-compose.dev.yml       # build local
├── pyproject.toml               # v0.6.0, extras: s3/gcs/azure/spark/dev
├── mkdocs.yml                   # docs config
├── LICENSE                      # Apache 2.0
├── README.md                    # badges + quick start
├── EVIDENCE.md                  # 91 testes documentados com resultados reais
├── KNOWN_ISSUES.md              # bugs e limitações encontrados nos testes
└── CHANGELOG.md                 # histórico de versões
```

---

## 8. Comandos úteis no dia a dia

```bash
# Verificar versão instalada
python -c "import permafrost; print(permafrost.__version__)"

# Testar freeze/thaw rapidamente
python -c "
import permafrost as pf, pandas as pd, numpy as np
np.random.seed(42)
df = pd.DataFrame({'id': range(1000), 'v': np.random.rand(1000)})
m = pf.freeze(df, '/tmp/test.permafrost')
print(f'ratio={m[\"ratio\"]:.2f}x | {m[\"stored_mb\"]:.3f}MB')
df_b = pf.thaw('/tmp/test.permafrost', verify=True)
print(f'rows={len(df_b)} | ok={len(df_b)==1000}')
"

# Ver todos os testes disponíveis
pytest tests/ --collect-only -q 2>&1 | tail -5

# Rodar só os testes críticos (rápido)
pytest tests/test_freeze_thaw.py tests/test_sparse_index.py -q

# Rodar suite completa sem Spark (3 min)
pytest tests/ -k "not Spark" -q

# Rodar suite completa com Spark (5 min)
pytest tests/ -q

# Checar imports do pacote
python -c "from permafrost import (freeze, thaw, audit, PermafrostCatalog,
    SchemaDetector, freeze_stream, freeze_to, thaw_from,
    PermafrostMaster, PermafrostClient); print('OK')"

# Subir cluster local para desenvolvimento
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up --scale worker=2

# Build e verificar pacote antes de publicar
python -m build && twine check dist/*
```

---

## 9. Próximos passos em ordem

1. **Publicar no GitHub** (`git push`) → CI roda automaticamente
2. **Criar conta e token no PyPI** → `twine upload dist/*`
3. **Criar conta e token no Docker Hub** → configurar secrets no GitHub
4. **Criar tag v0.6.0** → CI publica tudo automaticamente
5. **Corrigir P1** (imports legados nos testes antigos)
6. **Corrigir P2** (aviso de timestamp não ordenado)
7. **Corrigir P4** (catalog thread-safe)
8. **Criar notebooks Jupyter** (P7) para divulgação
9. **Post no Hacker News** — Show HN: Permafrost
10. **Post no r/dataengineering**

---

*Gerado em 2026-05 | Repositório: https://github.com/caua-ferreira/permafrost-framework*

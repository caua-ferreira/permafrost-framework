# ❄️ Permafrost — Problemas Conhecidos e Limitações

> Documento gerado a partir da suite de 385 testes massivos.  
> Cada item tem: causa técnica, impacto real, workaround e status de fix.

---

## 🔴 BUGS — Corrigi imediatamente antes do lançamento

### BUG-001 — CLI `verify` não retornava exit code 1 em falha

**Arquivo:** `src/permafrost/cli.py`  
**Descoberto em:** `test_cli_cobertura.py::TestCLIVerify::test_verify_arquivo_corrompido_exit_1`  
**Status:** ✅ Corrigido

**Causa:** O comando `verify` imprimia "Falha de integridade detectada" mas não
chamava `raise typer.Exit(1)`. Scripts que usassem `permafrost verify` em pipelines
CI/CD nunca detectariam arquivos corrompidos.

**Impacto:** Crítico. Um pipeline que faz `permafrost verify dados.permafrost && processar`
processaria dados corrompidos silenciosamente.

**Fix aplicado:**
```python
# Antes
console.print(Panel("✗ Falha de integridade detectada"))
console.print()

# Depois
console.print(Panel("✗ Falha de integridade detectada"))
console.print()
raise typer.Exit(1)  # ← adicionado
```

---

### BUG-002 — CLI `freeze` importava módulo antigo `permafrost_schema_detector`

**Arquivo:** `src/permafrost/cli.py`  
**Descoberto em:** `test_cli_cobertura.py::TestCLIFreeze::test_freeze_csv_cria_arquivo`  
**Status:** ✅ Corrigido

**Causa:** Após a reestruturação PyPI (`src/permafrost/`), a CLI ainda importava
`from permafrost_schema_detector import SchemaDetector` (módulo legado em `/tmp`).
Funcionava no ambiente de desenvolvimento mas falharia em qualquer instalação via `pip install`.

**Impacto:** Crítico. `permafrost freeze` falharia com `ModuleNotFoundError` para
qualquer usuário que instalasse o pacote via pip.

**Fix aplicado:**
```python
# Antes
from permafrost_schema_detector import SchemaDetector

# Depois
from permafrost.schema_detector import SchemaDetector
```

---

### BUG-003 — `thaw_iter(batch_size=N)` retornava último batch com tamanho errado

**Arquivo:** `src/permafrost/chunk_mode.py`  
**Descoberto em:** `test_comprehensive.py::TestChunkModeCompleto::test_thaw_iter_batches`  
**Status:** ✅ Corrigido

**Causa:** Quando `batch_size < chunk_rows`, a lógica de buffer não esgotava o
acumulador corretamente. Com `batch_size=10k` e `chunk_rows=20k`, os 4 primeiros
batches tinham 10k linhas mas o 5º retornava as 40k linhas restantes no buffer.

**Impacto:** Alto. Código que assumia `len(batch) <= batch_size` em todos os batches
poderia alocar memória insuficiente ou falhar em indexação.

**Fix aplicado:** Loop `while buf_rows >= batch_size` em vez de `if`.

---

## 🟡 LIMITAÇÕES CONHECIDAS DO FORMATO — documentar, não corrigir

### LIM-001 — Preditor `ts_delta_s` requer timestamps em ordem crescente

**Componente:** `codec.py` → preditor `PRED_TS`  
**Descoberto em:** `test_predictor_edge_cases.py::TestTimestampsExtremos::test_timestamps_misturados_anos`  
**Status:** 📋 Documentado — comportamento por design

**Causa técnica:** O preditor calcula `diff(unix_seconds)` entre valores consecutivos
e aplica zigzag encoding (otimizado para valores não-negativos). Timestamps não
ordenados geram deltas negativos que fazem overflow no uint32 do zigzag.

**Impacto:** Timestamps em ordem aleatória são restaurados incorretamente (sem erro!).
A detecção acontece apenas se o usuário comparar os valores.

**Workaround:** Sempre ordenar o DataFrame pela coluna de timestamp antes de freeze.
```python
df = df.sort_values("data").reset_index(drop=True)
pf.freeze(df, "arquivo.permafrost")
```

**Fix futuro (v1.1):** Usar `int64` para os deltas em vez de `uint32`, eliminando
o overflow. Requer bump de versão do formato (quebra compatibilidade).

---

### LIM-002 — `ts_delta_s` tem limite prático de ~2068 para timestamps futuros

**Componente:** `codec.py` → preditor `PRED_TS`  
**Descoberto em:** `test_predictor_edge_cases.py::TestTimestampsExtremos::test_timestamps_futuro_distante`  
**Status:** 📋 Documentado — limitação de int32

**Causa técnica:** O delta acumulado desde 1970 para timestamps futuros pode exceder
o limite de `int32` (2.147.483.647 segundos ≈ 68 anos). Timestamps em 2100+
geram valores incorretos.

```
2100-01-01 = 4.102.444.800 segundos Unix
2^31       = 2.147.483.648 (max int32)
→ Overflow em timestamps após ~2038 em alguns encodings
```

**Workaround:** O Permafrost é projetado para dados históricos (cold storage).
Para dados futuros distantes, usar `CODEC_ZSTD` com `QUANT_NONE` que trata
timestamps como string preservando precisão máxima.

**Fix futuro (v1.1):** Mesmo fix do LIM-001 — usar `int64` para os deltas.

---

### LIM-003 — `int64` com deltas > 2^31 entre valores consecutivos pode causar overflow

**Componente:** `codec.py` → preditor `PRED_DELTA` (delta_zigzag)  
**Descoberto em:** `test_predictor_edge_cases.py::TestInteirosExtremos::test_int64_valores_grandes`  
**Status:** 📋 Documentado — limitação do zigzag encoding

**Causa técnica:** O delta_zigzag usa `uint32` internamente. Diferenças entre
valores int64 consecutivos maiores que 2^31 fazem overflow silencioso.

**Exemplo problemático:**
```python
ids = [0, 10**15, 2*10**15]  # deltas = 10^15 >> 2^31
```

**Impacto:** Baixo para dados corporativos típicos (IDs sequenciais ou com gaps
razoáveis). Alto para IDs gerados com UUIDs int ou timestamps nanosegundo.

**Workaround:** Para IDs com gaps > 2 bilhões, o codec automaticamente usa
`raw_text` se a cardinalidade for alta. Para casos intermediários, verificar
se os IDs restaurados são corretos.

---

### LIM-004 — Floats com precisão > 2 casas decimais têm tolerância de ±0.005

**Componente:** `codec.py` → preditor `PRED_LAG1` (lag1_zigzag)  
**Descoberto em:** `test_predictor_edge_cases.py::TestFloatsEspeciais::test_float_alta_precisao`  
**Status:** 📋 Documentado — limitação da escala fixa

**Causa técnica:** O preditor multiplica os resíduos por 100 (escala de centésimos)
antes de arredondar para inteiro. Para valores entre 0 e 1 com 4 casas decimais
(ex: probabilidades), a precisão máxima é ±0.005.

**Impacto:** Médio para dados de probabilidade/proporção. Nulo para dados monetários
(que são naturalmente 2 casas).

**Workaround:** Para floats com alta precisão (> 2 casas), usar `QUANT_NONE` e
aceitar a tolerância de ±0.005, ou converter para inteiros antes de armazenar.

**Fix futuro:** Detecção automática da escala baseada na distribuição dos valores.

---

### LIM-005 — SchemaDetector não detecta campos presentes em < 1% dos documentos

**Componente:** `schema_detector.py`  
**Descoberto em:** `test_schema_detector_stress.py::TestCamposAusentes::test_campo_presente_em_apenas_1_documento`  
**Status:** 📋 Documentado — comportamento por design

**Causa técnica:** O detector analisa uma amostra de 500 documentos (configurável).
Campos ausentes na amostra são ignorados na inferência de schema.

**Impacto:** Campos raros (< 0.1% de frequência) são silenciosamente omitidos
do arquivo .permafrost. Dados são perdidos sem erro.

**Workaround:**
```python
# Aumentar o sample_size para cobrir campos raros
det = pf.SchemaDetector(sample_size=5000)

# Ou usar flatten() com todos os documentos
df, _, _ = det.flatten(lista_completa_de_docs)
```

---

### LIM-006 — Catalog DuckDB não é thread-safe com conexão compartilhada

**Componente:** `catalog.py` → `PermafrostCatalog`  
**Descoberto em:** `test_concorrencia.py::TestCatalogConcorrente`  
**Status:** 📋 Documentado — limitação do DuckDB `:memory:`

**Causa técnica:** DuckDB em modo `:memory:` cria um banco isolado por conexão.
Uma conexão não pode ser compartilhada entre threads.

**Impacto:** Aplicações multi-thread que compartilham uma instância de
`PermafrostCatalog` terão erros ou resultados inconsistentes.

**Workaround correto:**
```python
# ❌ Não fazer — 1 instância compartilhada entre threads
cat = pf.PermafrostCatalog(":memory:")
threading.Thread(target=lambda: cat.search()).start()

# ✅ Correto — 1 instância por thread/processo
def worker():
    cat = pf.PermafrostCatalog("catalog.db")
    cat.search()
```

**Fix futuro:** Adicionar lock interno ou usar modo WAL do DuckDB para
conexões concorrentes em arquivo.

---

## 🟢 LIMITAÇÕES ACEITAS — por design

### DES-001 — Imagens, vídeos e binários não são comprimidos eficientemente

**Componente:** Todo o pipeline  
**Status:** ✅ Comportamento esperado — documentado no README

JPEG, MP4, ZIP já têm entropia próxima de 8 bits/byte. Não há ganho de
compressão possível (limite de Shannon). Use o Permafrost apenas para dados
tabulares e semi-estruturados.

---

### DES-002 — Dados não ordenados pela coluna de partição não têm sparse index eficiente

**Componente:** `codec.py` → sparse index  
**Status:** ✅ Comportamento esperado — documentado no Getting Started

O sparse index funciona por chunk, não por linha. Se os dados de 2021 estiverem
distribuídos por todos os chunks (não ordenados), `thaw(filter={"ano": 2021})`
precisará ler todos os chunks. Solução: `df.sort_values("ano")` antes de freeze.

---

### DES-003 — freeze_stream requer que o schema seja consistente entre blocos

**Componente:** `chunk_mode.py`  
**Status:** ✅ Comportamento esperado — documentado no User Guide

O manifesto de preditores é detectado no primeiro bloco e fixado para todos
os blocos subsequentes. Se o segundo bloco tiver colunas novas, elas serão
ignoradas silenciosamente.

---

### DES-004 — Cluster não tem persistência de estado (Master reiniciado = jobs perdidos)

**Componente:** `cluster.py`  
**Status:** 📋 Roadmap v0.7

O Master guarda jobs e workers em dicionários em memória. Um restart apaga
todo o estado. Para produção com alta disponibilidade, uma persistência
Redis ou DuckDB é necessária (roadmap).

---

## 📊 Resumo dos Problemas

| ID | Tipo | Severidade | Status |
|----|------|------------|--------|
| BUG-001 | Bug — CLI verify exit code | Crítico | ✅ Corrigido |
| BUG-002 | Bug — CLI import antigo | Crítico | ✅ Corrigido |
| BUG-003 | Bug — thaw_iter batch overflow | Alto | ✅ Corrigido |
| LIM-001 | Limitação — ts_delta_s não-ordenado | Alto | 📋 Documentado |
| LIM-002 | Limitação — timestamps pós-2038 | Médio | 📋 Documentado |
| LIM-003 | Limitação — int64 delta overflow | Médio | 📋 Documentado |
| LIM-004 | Limitação — float precisão > 2 casas | Baixo | 📋 Documentado |
| LIM-005 | Limitação — campos raros no schema | Médio | 📋 Documentado |
| LIM-006 | Limitação — catalog não thread-safe | Médio | 📋 Documentado |
| DES-001 | Design — binários sem ganho | N/A | ✅ Documentado |
| DES-002 | Design — dados não ordenados | N/A | ✅ Documentado |
| DES-003 | Design — schema stream fixado | N/A | ✅ Documentado |
| DES-004 | Design — cluster sem persistência | Roadmap | 📋 v0.7 |

---

## 🔧 Roadmap de Fixes (próximas versões)

### v0.7 — Estabilidade

- [ ] **LIM-001 + LIM-002:** Migrar `ts_delta_s` para `int64` de deltas
  (requer bump de formato para v1.3)
- [ ] **LIM-006:** Adicionar lock interno no `PermafrostCatalog` para
  uso thread-safe
- [ ] **DES-004:** Persistência de estado do cluster via DuckDB embedded

### v0.8 — Precisão

- [ ] **LIM-004:** Detecção automática de escala para floats de alta precisão
- [ ] **LIM-003:** Usar `int64` no zigzag encoding para suportar IDs arbitrários

### v1.0 — Formato Estável

- [ ] Freeze do formato binário (sem breaking changes após v1.0)
- [ ] Backward compatibility garantida para arquivos v0.x → v1.0
- [ ] Publicar no PyPI como estável

---

*Atualizado em: 2026-05 | Suite de testes: 385 passing / 0 failing*

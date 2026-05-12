# Estudo de Algoritmos de Compressão

> Fase 01 do Permafrost Framework — Pesquisa e Benchmark

---

## Objetivo

Mapear o universo de algoritmos de compressão e definir quais combinações são ideais para dados frios de longo prazo, considerando:

- **Ratio de compressão** — quanto menor o arquivo final, melhor
- **Velocidade de compressão** — custo do freeze (aceitável ser lento)
- **Velocidade de decompressão** — custo do thaw (pode ser lento para cold data)
- **Longevidade do formato** — suporte garantido em 2040+
- **Decompressão parcial** — capacidade de ler blocos sem descomprimir tudo

---

## Algoritmos Avaliados

### Zstandard (Zstd)

- **Criador:** Yann Collet · Facebook/Meta · 2016
- **RFC:** RFC 8878
- **Categoria:** Híbrido (LZ77 + FSE/ANS)

**Como funciona:**
1. LZ77 com window de busca configurável (até 2GB com `--long`)
2. Finite State Entropy (FSE) — implementação de ANS que supera Huffman em ratio

**Níveis:**
| Nível | Ratio (dados corporativos) | Comp Speed | Decomp Speed |
|---|---|---|---|
| L1 | ~1.8× | 338 MB/s | 800+ MB/s |
| L3 | ~3.25× | 154 MB/s | 539 MB/s |
| L9 | ~3.67× | 30 MB/s | 505 MB/s |
| L19 | ~4.32× | 1-2 MB/s | 562 MB/s |

**Ponto crítico:** decompressão é **idêntica** independente do nível de compressão. L19 é a escolha óbvia para cold data.

**Papel no Permafrost:** codec padrão para warm tier. Nível 19 para freeze.

---

### LZMA2 / XZ

- **Criador:** Igor Pavlov · 2001 (LZMA) / 2009 (LZMA2/XZ)
- **Spec:** XZ format specification

**Como funciona:**
1. LZ77 com dictionary de até 4GB
2. Range Coding (arithmetic coding) — mais eficiente que Huffman em distribuições assimétricas
3. Markov model: estima probabilidade do próximo bit via contexto

**Benchmark no Permafrost (sobre Parquet, sem compressão interna):**
| Preset | Ratio | Comp Speed | Decomp Speed |
|---|---|---|---|
| preset=6 | 1.419× (sobre Parquet) | 3.2 MB/s | ~80 MB/s |
| preset=9\|EXTREME | 1.422× | 3.1 MB/s | ~80 MB/s |

**Papel no Permafrost:** codec padrão para cold tier (substituição do Zstd no L4).

---

### Brotli

- **Criador:** Google · 2013
- **RFC:** RFC 7932

**Como funciona:**
1. LZ77 com window de 16MB
2. Huffman de segunda ordem (context modeling)
3. Dicionário estático de 120KB para conteúdo web

**Notas:** melhor ratio para texto puro, mas lento demais para dados tabulares em compressão máxima (0.39 MB/s em Q11). Papel limitado no Permafrost — apenas para datasets predominantemente texto/JSON.

---

### ZPAQ

- **Criador:** Matt Mahoney · 2009
- **Método:** Context mixing (PAQ family)

**Como funciona:**
- 8+ modelos estatísticos independentes fazem previsões do próximo byte
- Um mixer neuronal pondera os modelos pelo histórico de acertos
- Output: o modelo mais preciso usa menos bits para representar cada símbolo

**Benchmark medido:**
- Sobre Parquet (sem compressão interna): **1.629× de ratio**
- Vs Zstd L19 (1.303×): **+25% de ratio**
- Velocidade de compressão: 0.3 MB/s (method=5)
- Velocidade de decompressão: ~30 MB/s

**Papel no Permafrost:** Vault tier — dados de compliance que nunca serão lidos.

---

### Apache Parquet

**Não é um codec de compressão** — é um formato colunar que **multiplica** a compressibilidade de qualquer codec.

**Encodings internos:**
- Dictionary encoding: `"Brasil"` (6 bytes) → `0` (1 byte)
- Delta encoding: `[2020-01-01, +5min, +5min, ...]` → `[ref, +300, +300, ...]`
- RLE + Bit-packing: runs de valores iguais colapsados
- Byte-stream split: intercala bytes de mesma posição em floats

**Resultado crítico medido:**
```
Parquet + Zstd L9 (compressão interna):   1291 KB
Parquet (sem compressão) + Zstd L19 ext:  1037 KB  ← -19.7% melhor!
```

**Por quê?** Compressão interna cria ilhas de dados aleatorizados. O Zstd externo recebe um mosaico e não encontra padrões cross-coluna. Sem compressão interna, o Zstd vê o arquivo inteiro como um stream contínuo — encontra padrões entre colunas.

---

## Experimento: "Comprimir o Já Comprimido"

Testamos empiricamente a hipótese de múltiplas camadas de compressão idêntica:

```python
raw_csv  = 3748.4 KB
layer_1  = 1020.6 KB  (Zstd L19)
layer_2  = 1020.7 KB  (+0.1 KB — PIOROU!)
layer_3  = 1020.7 KB  (+0.0 KB)
layer_5  = 1020.8 KB  (+0.2 KB total)
```

**Motivo:** dado comprimido com Zstd L19 tem entropia de **7.964 bits/byte** (máximo teórico: 8.0). Não há padrão restante para comprimir — apenas overhead de header cresce.

**Conclusão:** compressão em camadas só funciona quando cada camada opera em uma **abstração diferente** do dado.

---

## Ganhos Reais Medidos por Técnica

| Técnica | Ganho | Mecanismo |
|---|---|---|
| Dupla compressão (mesmo codec) | **0%** (piora +0.003%) | Entropia já máxima |
| Solid multi-arquivo | **+4.4%** | Contexto compartilhado entre chunks |
| Delta entre versões (dict) | **+6.4%** | v1 como dicionário para v2 |
| Deduplicação (40% dup) | **+11.0%** | Eliminação de redundância lógica |
| Parquet(none) + Zstd ext | **+19.7%** | Ordem correta das operações |
| Encoding semântico + Parquet + Zstd | **+34%** | Pipeline completo |

---

## Recomendação Final

| Tier | Codec | Quant | Use case |
|---|---|---|---|
| **Warm** | Zstd L3 | none | Dados acessados mensalmente |
| **Cold** | Zstd L19 | none | Dados acessados anualmente |
| **Archive** | LZMA2 extreme | none | Dados acessados raramente |
| **Vault** | ZPAQ method=5 | medium | Compliance — nunca lidos |

---

## Reproduzindo os Benchmarks

```bash
python benchmarks/01_compression_algorithms.py
python benchmarks/02_multilayer_experiment.py
python benchmarks/03_10gb_projection.py
```

Todos os resultados são salvos em `benchmarks/results/` como JSON.

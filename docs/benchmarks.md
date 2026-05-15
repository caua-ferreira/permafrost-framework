# Benchmarks

Todos os resultados abaixo são **medidos**, não estimados.
Reproduzíveis com `np.random.seed(42)` e os scripts em `benchmarks/`.

---

## Dataset de referência

```
80.000 linhas × 9 colunas
Tipos: int32, datetime64, int16, str (categoria), str (alta-cardinalidade),
       float64, str (categoria), str (categoria), float64
CSV original: 5.67 MB
```

---

## Compressão por codec e modo

| Configuração | Tamanho | Ratio | Redução | Freeze | Thaw |
|---|---|---|---|---|---|
| **LZMA2 lossless** | **0.678 MB** | **8.37×** | **88.0%** | 2.23s | 0.134s |
| LZMA2 vault (medium) | 0.558 MB | 10.17× | 90.2% | 2.10s | 0.128s |
| Zstd L19 lossless | 0.721 MB | 7.87× | 87.3% | 0.43s | 0.073s |

---

## Verificação de fidelidade (lossless)

| Campo | Preditor | Resultado |
|---|---|---|
| IDs (`id`) | `delta_zigzag` | ✅ exato — `max_diff = 0` |
| Timestamps (`data`) | `ts_delta_s` | ✅ exato — `100.0%` match |
| Floats (`total`) | `lag1_zigzag` | ✅ exato — `max_diff = 0.000000` |
| Categorias (`status`) | `category_u8` | ✅ exato — `100.0%` match |
| Strings (`regiao`) | `category_u8` | ✅ exato — `100.0%` match |

---

## Vault mode (semi-lossy)

| Campo | Comportamento | Tolerância |
|---|---|---|
| IDs | exatos | — |
| Categorias | exatas | — |
| Floats | arredondados para inteiro | ≤ R$0.50 |
| Timestamps | floor(minuto) | ≤ 60s |

---

## Sparse Index — thaw seletivo

Arquivo com 5 anos de dados (80k linhas), particionado por `ano`:

| Filtro | Linhas | % arq. lido | Tempo |
|---|---|---|---|
| `filter={"ano": 2020}` | 17.568 | **25.0%** | 0.046s |
| `filter={"ano": 2021}` | 17.520 | **30.6%** | 0.053s |
| `filter={"ano": 2022}` | 17.520 | **24.5%** | 0.043s |
| `filter={"ano": 2023}` | 17.520 | **31.2%** | 0.054s |
| `filter={"ano": 2024}` | 9.872  | **12.5%** | 0.024s |
| thaw completo | 80.000 | 100%   | 0.134s |

---

## Dados NoSQL — JSONL Social Media

```
5.000 posts (id, user_id, text, hashtags, mentions, likes, created_at, location)
```

| Abordagem | Tamanho | Ratio |
|---|---|---|
| JSONL raw | 1.44 MB | 1× |
| JSONL + LZMA2 direto | 0.046 MB | 31× |
| **Permafrost LZMA2** | **0.043 MB** | **33×** |

---

## Chunk Mode — streaming 300k linhas

| Métrica | Valor |
|---|---|
| Linhas processadas | 300.000 |
| Tamanho final | 1.018 MB |
| Ratio | 2.95× |
| **RAM pico** | **708 MB** (constante, independe do volume) |
| Tempo de freeze | 4.95s |
| Thaw completo | 300.000 linhas — IDs e valores corretos |
| thaw_iter (10 batches de 30k) | 300.000 linhas — OK |

---

## Cluster — 2 workers paralelos

| Métrica | Valor |
|---|---|
| Dataset | 30.000 linhas |
| Workers | 2 |
| Tasks | 3 (10k linhas cada) |
| Tempo total | 2.1s |
| Status | ✅ DONE — todas tasks concluídas |
| 3 jobs paralelos simultâneos | ✅ todos DONE |

---

## Custo estimado — Glacier Deep Archive

Para **0.678 MB** de dados comprimidos (original: 5.67 MB):

| Tier | $/GB/mês | Custo/mês |
|---|---|---|
| S3 Standard | $0.023 | $0.000016 |
| S3-IA | $0.0125 | $0.0000085 |
| Glacier | $0.004 | $0.0000027 |
| **Glacier Deep Archive** | **$0.00099** | **$0.00000067** |

**Para escala: 1 TB original → ~120 GB .permafrost → $0.12/mês no Glacier Deep Archive**  
*(vs $0.99/mês sem compressão — economia de 88%)*

---

## Reproduzir

```bash
git clone https://github.com/SEU_USUARIO/permafrost-framework
cd permafrost-framework
pip install -e '.[dev]'

# Dataset de referência
python scripts/generate_dataset.py --rows 80000 --output data/samples/test.csv

# Benchmarks
python benchmarks/01_compression_algorithms.py
python benchmarks/02_multilayer_experiment.py
python benchmarks/03_10gb_projection.py
```

---

## Contexto: por que o Permafrost supera LZMA2 puro?

A pergunta natural ao ver os benchmarks: **se o LZMA2 já existe, por que o Permafrost é melhor?**

### LZMA2 puro vs Permafrost — mesmo dado, mesmo codec

| Abordagem | Tamanho | Ratio |
|---|---|---|
| `lzma.compress(df.to_csv())` | 0.499 MB | **5.97×** |
| `pf.freeze(df, codec=CODEC_LZMA2)` | 0.284 MB | **10.50×** |

**A diferença é 76% de compressão adicional — sem trocar o algoritmo.**

O ganho vem dos preditores colunares que operam **antes** do codec:

```
Coluna "total" (floats): [199.50, 201.00, 198.75, 202.00, ...]

Abordagem ingênua → LZMA2 vê bytes de float64 (parecem aleatórios)
Permafrost →
  1. lag1_zigzag: calcula resíduos vs valor anterior
     → [0, +1.50, -2.25, +3.25, ...]   (valores pequenos!)
  2. multiplica por 100 → inteiros: [0, 150, -225, 325, ...]
  3. zigzag encode → uint32 sem sinal
  4. LZMA2 recebe inteiros pequenos → comprime muito melhor
```

Para uma coluna de status categórico ("Ativo", "Cancelado", "Pendente"):
- Sem Permafrost: LZMA2 vê strings repetidas de 6–9 bytes cada
- Com Permafrost: `category_u8` converte para índice 0, 1 ou 2 — **1 byte por linha**

### Quando o ZPAQ supera o LZMA2

O ZPAQ usa **context mixing** — 8+ modelos estatísticos simultâneos que aprendem
a probabilidade do próximo byte com base em padrões de longa distância.

Para logs de texto como `"INFO 2024-01-15T10:30:00Z auth service request id=000042"`,
a frase `"auth service request"` repete 5.000 vezes mas separada por muitos bytes.
O LZMA2 (janela de busca) perde esse padrão. O ZPAQ aprende e comprime 44% mais.

**Para dados tabulares**, os preditores colunares já eliminaram os padrões de longa
distância antes do codec. ZPAQ e LZMA2 empatan (diferença < 2%).

### Conclusão prática

| Tipo de dado | Codec ideal | Motivo |
|---|---|---|
| Dados corporativos tabulares | `CODEC_LZMA2` | Preditores já fizeram o trabalho |
| Logs de aplicação / texto longo | `CODEC_ZPAQ` | Context mixing ganha em padrões longos |
| Compliance — nunca será lido | `CODEC_ZPAQ` | Melhor ratio absoluto |
| Acesso frequente (warm storage) | `CODEC_ZSTD` | Decompressão 6× mais rápida |

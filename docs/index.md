# ❄️ Permafrost Data Framework

<div class="pf-badges" markdown>
[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework.svg)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml/badge.svg)](https://github.com/caua-ferreira/permafrost-framework/actions)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow)](https://pypi.org/project/permafrost-framework/)
</div>

**Plataforma distribuída de compressão inteligente para arquivamento digital de longo prazo.**

Comprime dados corporativos (SQL, NoSQL, redes sociais) em um formato binário auto-descritivo com **8–12× menos espaço**, integridade SHA-256 garantida, e leitura seletiva sem descomprimir o arquivo todo.

---

## Instalação

```bash
pip install permafrost-framework
```

```python
import permafrost as pf
```

---

## Quick Start (30 segundos)

=== "CSV / DataFrame"

    ```python
    import permafrost as pf
    import pandas as pd

    df = pd.read_csv("vendas.csv")   # qualquer tamanho

    # Comprimir
    metrics = pf.freeze(df, "vendas.permafrost",
                        codec=pf.CODEC_LZMA2,
                        partition_by="ano")
    print(f"Ratio: {metrics['ratio']:.2f}×  |  {metrics['stored_mb']:.2f} MB")
    # Ratio: 8.37×  |  0.67 MB

    # Descomprimir
    df_back = pf.thaw("vendas.permafrost", verify=True)

    # Ler apenas 2023 — sem descomprimir o resto
    df_2023 = pf.thaw("vendas.permafrost", filter={"ano": 2023})
    ```

=== "JSONL / NoSQL"

    ```python
    import permafrost as pf

    # Detecta automaticamente o schema
    det = pf.SchemaDetector()
    df, dtype, manifest = det.detect("posts.jsonl")
    # dtype = DataType.SEMI_STRUCTURED
    # Campos escalares → colunas com preditores
    # Arrays (hashtags, mentions) → JSON serializado

    metrics = pf.freeze(df, "posts.permafrost")
    print(f"Ratio: {metrics['ratio']:.2f}×")
    # 5.000 posts Twitter → 0.043 MB (ratio 33×)
    ```

=== "Datasets > RAM"

    ```python
    import permafrost as pf

    # Streaming — RAM constante, qualquer volume
    pf.freeze_file("100gb_dataset.csv", "arquivo.permafrost")

    # Ou via iterator (cursor de banco, API, etc.)
    def meu_cursor():
        while batch := conn.fetchmany(50_000):
            yield pd.DataFrame(batch)

    pf.freeze_stream(meu_cursor(), "arquivo.permafrost")

    # Iterar sem carregar tudo
    for batch in pf.thaw_iter("arquivo.permafrost", batch_size=50_000):
        processar(batch)
    ```

=== "Cloud (S3 / GCS / Azure)"

    ```python
    import permafrost as pf

    # Freeze direto para S3
    pf.freeze_to(df, "s3://meu-bucket/vendas_2024.permafrost",
                 partition_by="ano")

    # Thaw seletivo da cloud
    df_2024 = pf.thaw_from("s3://meu-bucket/vendas_2024.permafrost",
                            filter={"ano": 2024})

    # Audit sem download total (range requests)
    info = pf.audit_remote("s3://meu-bucket/vendas_2024.permafrost")
    print(info["orig_rows"], info["codec"])
    ```

---

## Por que o Permafrost?

### Problema

Empresas com grandes volumes de dados históricos pagam caro para mantê-los em formatos ineficientes (Excel, CSV, Parquet+Snappy). Esses dados raramente são acessados, mas precisam existir por compliance.

### Solução

O Permafrost comprime dados históricos em **5 camadas** que operam em abstrações diferentes — não é compressão em cima de compressão:

```
Dado bruto (CSV 5.85 MB)
    │
    ▼  L1: Encoding semântico por coluna
    │     IDs → delta_zigzag | Floats → lag1_zigzag
    │     Timestamps → ts_delta_s | Categorias → category_u8
    ▼  L2: Layout colunar (Parquet sem compressão interna)
    │     Bytes similares ficam juntos → mais padrões para L3
    ▼  L3: LZMA2 extreme / Zstd L19
    │     Entropy coding sobre o stream colunar
    ▼
  .permafrost (0.67 MB) — ratio 8.37×, redução 88%
```

### Benchmarks Medidos

| Dado | Original | .permafrost | Ratio |
|------|----------|-------------|-------|
| CSV corporativo (80k linhas) | 5.85 MB | **0.67 MB** | **8.37×** |
| JSONL social media (5k posts) | 1.44 MB | **0.043 MB** | **33×** |
| Dataset de vendas (300k linhas) | ~97 MB est. | **1.02 MB** | **~95×** |
| 1 TB em Glacier Deep Archive | $0.99/mês | **$0.12/mês** | **-88%** |

---

## O que o Permafrost suporta

| Feature | Status |
|---------|--------|
| CSV / DataFrame tabular | ✅ lossless, ratio 8–15× |
| JSONL / MongoDB / DynamoDB | ✅ flatten automático |
| Redes sociais (hashtags, nested) | ✅ schema detector |
| Sparse Index — thaw seletivo | ✅ 12–31% do arquivo por partição |
| Datasets > RAM (streaming) | ✅ RAM constante |
| Vault mode (semi-lossy) | ✅ ratio até 10×+ |
| Bit-rot detection (SHA-256) | ✅ antes de descomprimir |
| Catalog DuckDB | ✅ busca, custo, integridade |
| Cloud (S3 / GCS / Azure) | ✅ upload/download/range requests |
| Cluster distribuído | ✅ Master + Workers via FastAPI |
| PermafrostContext (v1.0) | ✅ API unificada: catalog + storage + cluster |
| Imagens / vídeos binários | ❌ entropia já máxima, sem ganho |

---

## Próximos passos

- [Getting Started](getting-started.md) — tutorial em 5 minutos
- [PermafrostContext](api-reference/context.md) — API unificada (v1.0)
- [Freeze & Thaw](user-guide/freeze-thaw.md) — API core
- [SQL & NoSQL](user-guide/nosql.md) — JSONL, MongoDB, redes sociais
- [Cloud Storage](user-guide/cloud.md) — S3, GCS, Azure

---

## Quando o Permafrost faz sentido

### O que o Permafrost entrega que nenhum codec sozinho entrega

Comprimir um CSV com LZMA2 puro é simples — qualquer pessoa faz com uma linha de Python.
O problema é que você obtém **5.97×** de ratio.

O Permafrost entrega **10.50×** no mesmo dado, com lossless garantido.

A diferença não está no codec — está no que acontece **antes** do codec.
Os preditores colunares transformam cada coluna pelo seu significado semântico:

```
Preços: [199.50, 201.00, 198.75, 202.00, ...]
→ lag1_zigzag → resíduos: [0, +1.50, -2.25, +3.25, ...]
→ valores pequenos perto de zero → LZMA2 comprime quase perfeitamente
```

Sem isso, o LZMA2 recebe floats que parecem aleatórios.
Com isso, recebe deltas pequenos e previsíveis.

**Isso nenhum compressor genérico faz.** `lzma.compress(df.to_csv())` entrega 5.97×.
O Permafrost entrega 10.50× no mesmo dado, com sparse index, SHA-256 e catalog.

---

### Os três diferenciais reais

**1. Sparse Index — leitura seletiva sem full-restore**

Com um arquivo `.tar.gz` ou `.zst` contendo 10 anos de dados, você é obrigado a
descomprimir tudo para acessar 2021. Com o Permafrost:

```python
df_2021 = pf.thaw("historico.permafrost", filter={"ano": 2021})
# Lê apenas 12–31% do arquivo — os outros anos nem são tocados
```

**2. Catalog — descoberta sem baixar**

Com 500 arquivos `.permafrost` no S3, você consulta o catalog local (DuckDB)
e sabe exatamente qual arquivo baixar — por schema, período, codec, custo —
antes de fazer um único request de rede.

**3. Integridade embutida — SHA-256 antes de descomprimir**

Você descobre bit-rot em 2031 **antes** de gastar CPU descomprimindo 2 GB de arquivo
corrompido. A verificação falha em milissegundos, não em minutos.

---

### Quando **não** usar o Permafrost

| Situação | Por quê não | Alternativa |
|----------|-------------|-------------|
| Imagens, vídeos, PDFs | Entropia já máxima — sem ganho possível | Armazenar direto |
| Dados < 1 MB | Overhead do formato não compensa | `gzip` |
| Pipeline já madura com Parquet+Snappy+S3 | Custo de migração sem ganho proporcional | Manter o que tem |
| Dados que mudam frequentemente | Permafrost é para cold data — imutável | Parquet no data lake |
| Texto livre de alta variedade | ZPAQ ajuda, mas gain é menor | Avaliar caso a caso |

---

### Resumo

O Permafrost resolve um problema específico: **dados corporativos históricos que precisam
existir por anos, ser consultáveis sem full-restore, e ter integridade garantida ao longo do tempo.**

Para esse caso, ele entrega algo que não existe pronto no ecossistema Python.
Para guardar um CSV e nunca mais abrir — `gzip` resolve com menos trabalho.

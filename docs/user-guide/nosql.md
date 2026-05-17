# SQL & NoSQL — JSONL, MongoDB, Redes Sociais

O `SchemaDetector` detecta automaticamente o tipo de dado e aplica a estratégia
de encoding correta. O `.permafrost` resultante é idêntico ao de dados tabulares.

---

## Dados tabulares (CSV, DataFrame)

```python
import permafrost as pf

# CSV
det = pf.SchemaDetector()
df, dtype, manifest = det.detect("vendas.csv")
# dtype = DataType.TABULAR

# DataFrame direto
df, dtype, manifest = det.detect(meu_dataframe)
```

---

## JSONL (JSON Lines)

Formato usado por dumps do MongoDB, Elasticsearch, APIs REST, redes sociais:

```python
import permafrost as pf

det = pf.SchemaDetector()
df, dtype, manifest = det.detect("posts.jsonl")
# dtype = DataType.SEMI_STRUCTURED

print(f"Detectado: {dtype}")
print(f"Colunas: {list(df.columns)}")
print(f"Linhas: {len(df):,}")
```

### Como o flatten funciona

Dado um documento como:

```json
{
  "id": "1001",
  "user_id": 42,
  "text": "Novo projeto open source!",
  "hashtags": ["#python", "#data"],
  "mentions": ["@alice"],
  "likes": 150,
  "created_at": "2024-01-15T10:30:00Z",
  "location": {"lat": -23.5, "lon": -46.6, "city": "São Paulo"}
}
```

O SchemaDetector gera:

| Campo | Tipo | Estratégia | Preditor |
|-------|------|------------|----------|
| `id` | str | column_scalar | `raw_text` |
| `user_id` | int | column_scalar | `delta_zigzag` |
| `text` | str | column_scalar | `raw_text` |
| `likes` | int | column_scalar | `delta_zigzag` |
| `created_at` | datetime | column_scalar | `ts_delta_s` |
| `hashtags` | array | json_str | `raw_text` |
| `mentions` | array | json_str | `raw_text` |
| `location` | nested | json_str | `raw_text` |

**Campos escalares** → colunas com preditores colunares completos (máximo ratio).  
**Arrays e nested** → JSON serializado por coluna (LZMA2 ainda encontra padrões).

### Freeze de JSONL

```python
import permafrost as pf

det = pf.SchemaDetector()
df, dtype, manifest = det.detect("posts.jsonl")

metrics = pf.freeze(df, "posts.permafrost", codec=pf.CODEC_LZMA2)
print(f"Ratio: {metrics['ratio']:.2f}×")
# 5.000 posts Twitter → ratio 33×
```

---

## MongoDB dump

```python
import permafrost as pf
import json

# Dump do mongodump --out=dump.jsonl
det = pf.SchemaDetector()
df, dtype, manifest = det.detect("colecao.jsonl")
metrics = pf.freeze(df, "colecao.permafrost", partition_by="created_year")

# Ou via pymongo diretamente
from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017/")
col = client["meu_banco"]["pedidos"]

docs = list(col.find({}, {"_id": 0}))
df, dtype, manifest = det.flatten(docs)
metrics = pf.freeze(df, "pedidos.permafrost")
```

---

## Dados de redes sociais

```python
import permafrost as pf, json

det = pf.SchemaDetector()

# Twitter/X export
df, _, _ = det.detect("tweets.jsonl")
pf.freeze(df, "tweets.permafrost")

# Instagram export (JSON)
with open("instagram_export.json") as f:
    posts = json.load(f)["media"]
df, _, _ = det.flatten(posts)
pf.freeze(df, "instagram.permafrost")
```

---

## Recuperar dados originais (thaw → reconstruct)

Após o thaw, os campos array e nested estão como JSON strings.
Para reconstruir os documentos originais:

```python
import permafrost as pf
import json

df = pf.unfreeze("posts.permafrost")

# Reconstruir documentos
def reconstruct(row):
    doc = row.to_dict()
    # Desserializar campos que foram JSON-encoded
    for col in ["hashtags", "mentions", "location"]:
        if col in doc and isinstance(doc[col], str):
            doc[col] = json.loads(doc[col])
    return doc

documentos = [reconstruct(df.iloc[i]) for i in range(len(df))]
```

---

## Benchmark — JSONL Social Media

| Dado | Original | .permafrost | Ratio |
|------|----------|-------------|-------|
| 5.000 posts (hashtags, mentions, nested location) | 1.44 MB | **0.043 MB** | **33×** |
| JSONL + LZMA2 direto (sem Permafrost) | 1.44 MB | 0.046 MB | 31× |
| Vantagem Permafrost | — | **+7% menor** | **+ thaw seletivo** |

!!! info "Por que o Permafrost vence"
    A diferença de ratio não é dramática para JSON puro. O ganho real é o **thaw seletivo**:
    com o Permafrost você lê só os posts de uma data específica sem descomprimir tudo.
    Com JSONL+LZMA2 você é obrigado a descomprimir e varrer tudo.

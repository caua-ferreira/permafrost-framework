# Diff — Comparar Versões

`pf.diff()` compara dois arquivos `.permafrost` e retorna exatamente o que mudou: linhas inseridas, deletadas e alteradas.

---

## Uso básico

```python
import permafrost as pf

# Resumo em formato dict
resultado = pf.diff("vendas_jan.pf", "vendas_fev.pf", output="summary")
print(resultado)
# {'deleted': 42, 'inserted': 89, 'changed': 317, 'unchanged': 49552}
```

---

## Formatos de saída

### `output="dict"` (padrão)

```python
r = pf.diff("v1.pf", "v2.pf")

r["deleted"]          # pd.DataFrame — linhas presentes em v1, ausentes em v2
r["inserted"]         # pd.DataFrame — linhas novas em v2
r["changed"]          # pd.DataFrame — linhas com valores diferentes
                      #   colunas alteradas aparecem como col_v1 / col_v2
r["unchanged_count"]  # int
r["summary"]          # dict com contagens
```

### `output="dataframe"`

```python
df = pf.diff("v1.pf", "v2.pf", output="dataframe")
# DataFrame com coluna _diff: "inserted" | "deleted" | "changed"
print(df.groupby("_diff").size())
```

### `output="summary"`

Retorna apenas `dict` com contagens — não carrega as linhas em memória. Útil para arquivos muito grandes:

```python
s = pf.diff("v1.pf", "v2.pf", output="summary")
print(s)  # {'deleted': 42, 'inserted': 89, 'changed': 317, 'unchanged': 49552}
```

---

## Chave de join

O diff precisa saber qual coluna identifica unicamente cada linha para fazer o match entre v1 e v2.

**Prioridade:**
1. Parâmetro `on=` explícito
2. `primary_key` embutida no arquivo (definida em `pf.freeze(..., primary_key=...)`)
3. Fallback posicional (por índice linha) — emite `UserWarning`

```python
# Usar primary_key do arquivo
pf.diff("v1.pf", "v2.pf")

# Especificar manualmente
pf.diff("v1.pf", "v2.pf", on="id")

# Chave composta
pf.diff("v1.pf", "v2.pf", on=["empresa_id", "produto_id"])
```

---

## Colunas alteradas

Por padrão, no DataFrame de `changed`, todas as colunas aparecem duplicadas (`_v1` / `_v2`). Para ver apenas as colunas que de fato mudaram:

```python
r = pf.diff("v1.pf", "v2.pf", changed_columns_only=True)
```

---

## Filtrar tipos de diferença

```python
# Ver apenas linhas novas
r = pf.diff("v1.pf", "v2.pf", include=["inserted"])

# Ver apenas alterações e deleções
r = pf.diff("v1.pf", "v2.pf", include=["changed", "deleted"])
```

---

## Tolerância em floats

Comparações de ponto flutuante usam tolerância relativa `rtol=1e-9` por padrão. Para dados com imprecisão aceitável:

```python
pf.diff("v1.pf", "v2.pf", rtol=1e-4)
```

---

## Exemplo completo

```python
import permafrost as pf

# Criar dois snapshots
pf.freeze(df_janeiro, "jan.pf", primary_key="id")
pf.freeze(df_fevereiro, "fev.pf", primary_key="id")

# Diff completo
r = pf.diff("jan.pf", "fev.pf")

print(f"Novos clientes:    {r['summary']['inserted']}")
print(f"Clientes perdidos: {r['summary']['deleted']}")
print(f"Registros mudados: {r['summary']['changed']}")

# Inspecionar alterações
df_changed = r["changed"]
print(df_changed[["id", "status_v1", "status_v2", "mrr_v1", "mrr_v2"]].head(10))
```

---

## Ver também

- [`pf.append()`](append.md) — escrever dados novos em um arquivo existente
- [Exemplo completo](../../examples/05_append_diff.py)

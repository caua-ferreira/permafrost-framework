# Query SQL

`pf.query()` executa SQL diretamente sobre arquivos `.permafrost` / `.pf` usando **DuckDB** como motor. Não é necessário chamar `unfreeze()` primeiro.

---

## Uso básico

Referencie o arquivo pelo caminho dentro da query, entre aspas simples:

```python
import permafrost as pf

df = pf.query("SELECT * FROM 'vendas.pf' WHERE ano = 2024 LIMIT 100")
```

O resultado é sempre um `pd.DataFrame`.

---

## Agregações e filtros

```python
# Receita por região
df = pf.query("""
    SELECT regiao,
           COUNT(*)               AS transacoes,
           ROUND(SUM(total), 2)   AS receita,
           ROUND(AVG(total), 2)   AS ticket_medio
    FROM 'vendas.pf'
    GROUP BY regiao
    ORDER BY receita DESC
""")

# Top 10 produtos no mês de março
df = pf.query("""
    SELECT produto, SUM(quantidade) AS unidades
    FROM 'vendas.pf'
    WHERE mes = 3
    GROUP BY produto
    ORDER BY unidades DESC
    LIMIT 10
""")
```

---

## JOIN entre arquivos

Cada arquivo é uma "tabela virtual". É possível fazer JOIN entre múltiplos arquivos `.pf` na mesma query:

```python
df = pf.query("""
    SELECT v.regiao,
           ROUND(SUM(v.total), 2)  AS receita,
           m.meta_anual,
           ROUND(100.0 * SUM(v.total) / m.meta_anual, 1) AS pct_atingido
    FROM 'vendas.pf'  v
    JOIN 'metas.pf'   m ON v.regiao = m.regiao
    GROUP BY v.regiao, m.meta_anual
    ORDER BY pct_atingido DESC
""")
```

---

## Alias com `register()`

Para evitar repetir o caminho na query:

```python
pf.register("vendas", "data/vendas_2024.pf")
pf.register("metas",  "data/metas_2024.pf")

df = pf.query("SELECT * FROM vendas WHERE total > 1000")
df = pf.query("SELECT v.*, m.meta FROM vendas v JOIN metas m ON v.regiao = m.regiao")

# Listar aliases registrados
print(pf.registered())

# Remover alias
pf.unregister("vendas")
```

---

## Window functions e CTEs

DuckDB suporta SQL completo, incluindo CTEs e window functions:

```python
# Share de mercado por produto
df = pf.query("""
    WITH totais AS (
        SELECT produto, SUM(total) AS receita
        FROM 'vendas.pf'
        GROUP BY produto
    )
    SELECT produto,
           receita,
           ROUND(100.0 * receita / SUM(receita) OVER (), 1) AS share_pct
    FROM totais
    ORDER BY receita DESC
""")

# Rank de vendedores por região
df = pf.query("""
    SELECT vendedor, regiao, SUM(total) AS receita,
           RANK() OVER (PARTITION BY regiao ORDER BY SUM(total) DESC) AS rank_na_regiao
    FROM 'vendas.pf'
    GROUP BY vendedor, regiao
""")
```

---

## Exportar resultado

O resultado é um DataFrame padrão do pandas — use qualquer método de exportação:

```python
df = pf.query("SELECT regiao, SUM(total) AS receita FROM 'vendas.pf' GROUP BY regiao")

df.to_csv("resultado.csv", index=False)
df.to_excel("resultado.xlsx", index=False)
```

Ou combine com `unfreeze()` para exportar um subset filtrado diretamente para XLSX/CSV sem passar pelo DataFrame:

```python
xlsx = pf.unfreeze("vendas.pf", filter={"ano": 2024}, output_format="xlsx")
with open("vendas_2024.xlsx", "wb") as f:
    f.write(xlsx)
```

---

## Backend

O motor padrão é **DuckDB**. Para trocar:

```python
pf.set_query_backend("duckdb")   # padrão
```

---

## Ver também

- [API Reference — query()](../api-reference/core.md#query)
- [Exemplo completo](../../examples/06_query_sql.py)

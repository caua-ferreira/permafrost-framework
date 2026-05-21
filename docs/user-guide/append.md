# Append — Escrita Incremental

`pf.append()` adiciona novos dados a um arquivo `.permafrost` existente **sem recomprimir os dados originais**. É a forma recomendada para pipelines que acumulam dados ao longo do tempo.

---

## Uso básico

```python
import permafrost as pf
import pandas as pd

# Arquivo já existe com dados de janeiro
result = pf.append("logs.pf", df_fevereiro)

print(result["total_rows"])   # total acumulado
print(result["new_chunks"])   # chunks adicionados nesta operação
```

---

## Como funciona internamente

1. Abre o arquivo existente e lê o sparse index
2. Verifica SHA-256 dos chunks originais (`verify=True` por padrão)
3. Comprime `df` nos novos chunks e os escreve no final do arquivo
4. Faz **patch no header** (`n_chunks` e `orig_rows`) sem reescrever o arquivo inteiro
5. Reescreve o sparse index com as novas entradas

O resultado é um arquivo `.permafrost` válido que o `unfreeze()`, `peek()` e `query()` leem normalmente.

---

## Particionamento após append

Se o arquivo original foi criado com `partition_by=`, o append respeita a mesma coluna de partição. Todos os thaws seletivos continuam funcionando:

```python
pf.freeze(df_jan, "vendas.pf", partition_by="mes")
pf.append("vendas.pf", df_fev)  # adiciona mes=2 ao sparse index
pf.append("vendas.pf", df_mar)  # adiciona mes=3

# Thaw seletivo funciona normalmente
df = pf.unfreeze("vendas.pf", filter={"mes": 2})
```

---

## Compatibilidade de schema

O schema (conjunto de colunas e tipos) do DataFrame novo deve ser compatível com o original. Se houver divergência, um `ValueError` é levantado antes de qualquer escrita:

```python
# Erro: coluna extra não existia no original
pf.append("vendas.pf", df_com_coluna_nova)
# ValueError: schema mismatch — colunas extras: {'desconto'}
```

---

## Múltiplos appends

```python
for mes_df in [df_jan, df_fev, df_mar, df_abr]:
    res = pf.append("historico.pf", mes_df)
    print(f"Total acumulado: {res['total_rows']:,}")
```

---

## Verificação de integridade

Por padrão (`verify=True`), o append verifica o SHA-256 de todos os chunks existentes antes de escrever. Para arquivos grandes onde a integridade já foi verificada recentemente:

```python
result = pf.append("historico.pf", df_novo, verify=False)
```

!!! warning
    Use `verify=False` apenas quando tiver certeza da integridade do arquivo.

---

## Ver também

- [`pf.diff()`](diff.md) — comparar duas versões de um arquivo
- [Exemplo completo](../../examples/05_append_diff.py)

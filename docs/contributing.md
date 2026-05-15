# Contributing

Obrigado pelo interesse em contribuir com o Permafrost!

---

## Setup do ambiente

```bash
git clone https://github.com/SEU_USUARIO/permafrost-framework
cd permafrost-framework
pip install -e '.[dev]'
```

---

## Rodando os testes

```bash
# Suite completa
pytest tests/ -v

# Testes específicos
pytest tests/test_freeze_thaw.py -v
pytest tests/test_catalog.py -v
pytest tests/test_cluster.py -v

# Com cobertura
pytest tests/ --cov=src/permafrost --cov-report=html
```

---

## Estrutura do projeto

```
src/permafrost/
    __init__.py          # Expõe a API pública
    codec.py             # freeze(), thaw(), audit(), preditores
    catalog.py           # PermafrostCatalog (DuckDB)
    chunk_mode.py        # freeze_stream(), freeze_file(), thaw_iter()
    cli.py               # CLI (typer + rich)
    cluster.py           # Master, Worker, Client
    schema_detector.py   # SchemaDetector, DataType
    storage.py           # Adapters de cloud storage
```

---

## Como adicionar um novo codec

1. Escolher o próximo `codec_id` disponível em `codec.py`
2. Implementar `_compress_bytes()` e `_decompress_bytes()` para o novo codec
3. Adicionar a constante `CODEC_MEUCODEC = 0x0N`
4. Exportar a constante em `__init__.py`
5. Adicionar ao mapa de nomes em `audit()`
6. Escrever testes em `tests/test_freeze_thaw.py`

---

## Como adicionar um novo StorageAdapter

1. Criar classe que herda de `StorageAdapter` em `storage.py`
2. Implementar todos os métodos abstratos (`upload`, `download`, `exists`, etc.)
3. Adicionar ao factory `storage_from_uri()` com o novo scheme URI
4. Exportar em `__init__.py`
5. Adicionar dependência opcional em `pyproject.toml`

---

## Processo de PR

1. Fork + branch descritiva (`feat/zpaq-codec`, `fix/timestamp-encoding`)
2. Código + testes (sem regressão nos 91 testes existentes)
3. `pytest tests/ -v` deve passar completamente
4. PR com descrição do que e por quê

---

## Code of Conduct

Este projeto segue o [Contributor Covenant](https://www.contributor-covenant.org/).
Seja respeitoso, construtivo e inclusivo.

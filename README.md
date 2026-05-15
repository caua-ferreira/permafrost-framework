# ❄️ Permafrost Data Framework

<div align="center">

[![PyPI version](https://badge.fury.io/py/permafrost-framework.svg)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://github.com/SEU_USUARIO/permafrost-framework/actions/workflows/tests.yml/badge.svg)](https://github.com/SEU_USUARIO/permafrost-framework/actions)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow)](https://pypi.org/project/permafrost-framework/)
[![Docs](https://img.shields.io/badge/docs-mkdocs-00D4FF)](https://SEU_USUARIO.github.io/permafrost-framework)

**Plataforma distribuída de compressão inteligente para arquivamento digital de longo prazo.**

</div>

## Instalação

```bash
pip install permafrost-framework
```

## Quick Start

```python
import permafrost as pf

metrics = pf.freeze(df, "vendas.permafrost", codec=pf.CODEC_LZMA2, partition_by="ano")
print(f"Ratio: {metrics['ratio']:.2f}x")

df_back = pf.thaw("vendas.permafrost", verify=True)
df_2023 = pf.thaw("vendas.permafrost", filter={"ano": 2023})  # sparse index
```

## Benchmarks (medidos)

| Dado | Original | .permafrost | Ratio |
|------|----------|-------------|-------|
| CSV corporativo 80k linhas | 5.85 MB | **0.678 MB** | **8.37x** |
| JSONL social media 5k posts | 1.44 MB | **0.043 MB** | **33x** |
| 1 TB no Glacier Deep Archive | $0.99/mês | **$0.12/mês** | **-88%** |

91/91 testes passando — ver [EVIDENCE.md](EVIDENCE.md).

## Docs

[SEU_USUARIO.github.io/permafrost-framework](https://SEU_USUARIO.github.io/permafrost-framework)

## Licença

Apache License 2.0

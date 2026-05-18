# ❄️ Permafrost Framework

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/permafrost-framework/)
[![Downloads](https://static.pepy.tech/badge/permafrost-framework/month)](https://pepy.tech/project/permafrost-framework)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)

**Plateforme de compression intelligente pour l'archivage numérique à long terme.**

*210 millions de lignes : Permafrost + LZMA2 = 3,03 Go vs CSV = 16,35 Go (5,4×) — près de 2× mieux que Parquet. Interrogez une seule année sur 5 ans de données : 42M lignes lues, seulement 20% du fichier consulté.*

🌐 [English](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.md) · [Português (BR)](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.pt-BR.md) · [Español](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.es.md) · **Français** · [中文](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.zh-CN.md) · [العربية](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.ar.md) · [हिन्दी](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.hi.md)

[Documentation](https://caua-ferreira.github.io/permafrost-framework) · [Démarrage rapide](#démarrage-rapide) · [Benchmarks](#benchmarks) · [API](#référence-api)

</div>

---

## Qu'est-ce que Permafrost ?

Les données historiques d'entreprise — CSV, JSONL, exports de bases de données — restent en cold storage (S3 Glacier, Azure Archive) pendant des années à un coût élevé. Le problème : pour accéder aux données d'un seul mois dans un fichier de 10 Go, il faut tout décompresser.

Permafrost résout ce problème avec deux mécanismes :

1. **Prédicteurs de colonnes** — transforme sémantiquement les données avant la compression (delta, zigzag, timestamps, catégories), atteignant des ratios bien supérieurs au LZMA2 pur
2. **Sparse index** — un index intégré dans le fichier qui pointe vers l'octet exact de chaque chunk, permettant des lectures sélectives via HTTP Range Requests sans télécharger le fichier entier

```
210 000 000 lignes × 13 colonnes — benchmark réel mesuré localement :

CSV brut :                  16,35 Go  (1,00×)
Parquet + Snappy :           5,89 Go  (2,78×)   écriture :  8,9 min
CSV + LZMA2 pur (p9) :     ~3,80 Go  (~4,3×)   écriture :  ~7 h  ⚠️ impraticable
Permafrost + ZSTD :          3,25 Go  (5,03×)   écriture : 77,7 min
Permafrost + LZMA2 :         3,03 Go  (5,40×)   écriture : 93,5 min   ← près de 2× mieux que Parquet

Lire uniquement l'année 2022 → 42M lignes en 5,7 min — seulement 20% du fichier consulté
```

---

## Fonctionnalités

- **Haute compression** — prédicteurs de colonnes (delta_zigzag, lag1_zigzag, ts_delta_s, category_u8) avant Zstd / LZMA2 / ZPAQ
- **Lectures sélectives** — sparse index intégré permettant `filter={"année": 2023}` sans décompresser le reste
- **Intégrité garantie** — SHA-256 par chunk, vérifié avant toute décompression
- **Auto-descriptif** — schéma Arrow complet intégré dans le fichier ; lisible en 2040 sans documentation externe
- **Cloud-native** — support natif pour S3, Google Cloud Storage et Azure Blob Storage avec HTTP Range Requests
- **Catalogue DuckDB** — recherche de métadonnées sur des centaines de fichiers distants sans rien télécharger
- **Streaming** — traite des jeux de données plus grands que la RAM avec `freeze_file()` et `peek()`
- **Cluster distribué** — Master + Workers via FastAPI ; traite 1 To en parallèle avec N workers
- **Chiffrement** — AES-256-GCM par chunk, surcoût de stockage de 0,00%

---

## Installation

```bash
# Installation de base
pip install permafrost-framework

# Avec support AWS S3
pip install "permafrost-framework[s3]"

# Avec support Google Cloud Storage
pip install "permafrost-framework[gcs]"

# Avec support Azure Blob Storage
pip install "permafrost-framework[azure]"

# Tous les fournisseurs cloud
pip install "permafrost-framework[all-cloud]"
```

**Prérequis :** Python 3.10+

---

## Démarrage Rapide

### Freeze et Unfreeze de base

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("historique_ventes.csv")

# Compresser — retourne des métriques
metrics = pf.freeze(df, "ventes.permafrost", codec=pf.CODEC_LZMA2, partition_by="année")
print(f"Ratio : {metrics['ratio']:.2f}×  |  {metrics['original_mb']:.1f} Mo → {metrics['stored_mb']:.1f} Mo")

# Décompresser tout
df_back = pf.unfreeze("ventes.permafrost", verify=True)

# Décompresser uniquement 2023 — ne lit que les chunks de cette année
df_2023 = pf.unfreeze("ventes.permafrost", filter={"année": 2023})
```

### Streaming (jeux de données plus grands que la RAM)

```python
# Compresser un grand fichier sans le charger en mémoire
pf.freeze_file("100go.csv", "sortie.permafrost", chunk_rows=50_000)

# Itérer par lots
for batch_df in pf.peek("sortie.permafrost", batch_size=50_000):
    traiter(batch_df)
```

### Cloud (S3, GCS, Azure)

```python
# Envoyer directement sur S3
pf.freeze_to(df, "s3://mon-bucket/données/ventes.permafrost")

# Lecture sélective depuis S3 — ne télécharge pas le fichier entier
df_2023 = pf.thaw_from("s3://mon-bucket/données/ventes.permafrost", filter={"année": 2023})
```

### CLI

```bash
# Compresser
permafrost freeze ventes.csv ventes.permafrost --codec lzma2 --partition-by année

# Décompresser avec filtre
permafrost unfreeze ventes.permafrost --filter '{"année": 2023}' --output ventes_2023.csv

# Auditer (sans décompresser)
permafrost audit ventes.permafrost
```

---

## Benchmarks

### Compression vs. alternatives

| Format | Taille | Ratio | Temps d'écriture |
|--------|--------|-------|-----------------|
| CSV brut | 16,35 Go | 1,00× | — |
| Parquet + Snappy | 5,89 Go | 2,78× | 8,9 min |
| CSV + LZMA2 pur *(p9)* | ~3,80 Go | ~4,3× | **~7 h** ⚠️ |
| **Permafrost + ZSTD** | **3,25 Go** | **5,03×** | **77,7 min** |
| **Permafrost + LZMA2** | **3,03 Go** | **5,40×** | **93,5 min** |

### Coût de stockage cloud (S3 Glacier Deep Archive)

| Volume original | Sans Permafrost | Avec Permafrost (5,4×) | Économies mensuelles |
|-----------------|-----------------|------------------------|---------------------|
| 1 To | $0,99 | **$0,18** | **-81%** |
| 10 To | $9,90 | **$1,83** | **-81%** |
| 100 To | $99,00 | **$18,33** | **-81%** |

---

## Référence API

| Fonction | Description |
|----------|-------------|
| `pf.freeze(df, path, ...)` | Compresse un DataFrame vers `.permafrost` |
| `pf.unfreeze(path, filter=None)` | Décompresse ; `filter` utilise le sparse index |
| `pf.audit(path)` | Retourne les métadonnées sans décompresser |
| `pf.freeze_append(path, df_new)` | Ajoute des lignes à un fichier existant |
| `pf.peek(path, batch_size=50_000)` | Décompresse par lots itératifs |
| `pf.freeze_to(df, uri)` | Compresse et envoie directement dans le cloud |
| `pf.thaw_from(uri, filter=None)` | Décompresse depuis le cloud avec Range Request |

---

## Contribuer

Les contributions sont les bienvenues ! Consultez le [guide de contribution](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md).

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Licence

Apache License 2.0 — voir [LICENSE](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE).

---

<div align="center">

Fait avec ❄️ pour des données qui doivent durer des décennies.

</div>

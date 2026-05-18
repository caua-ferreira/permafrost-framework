# ❄️ Permafrost Framework

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/permafrost-framework/)
[![Downloads](https://static.pepy.tech/badge/permafrost-framework/month)](https://pepy.tech/project/permafrost-framework)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)

**दीर्घकालिक डिजिटल संग्रह के लिए बुद्धिमान संपीड़न मंच।**

*21 करोड़ पंक्तियाँ: Permafrost + LZMA2 = 3.03 GB बनाम CSV = 16.35 GB (5.4×) — Parquet से लगभग 2× बेहतर। 5 साल के डेटा से एक साल की क्वेरी: 4.2 करोड़ पंक्तियाँ पढ़ी गईं, केवल 20% फ़ाइल स्पर्श की गई।*

🌐 [English](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.md) · [Português (BR)](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.pt-BR.md) · [Español](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.es.md) · [Français](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.fr.md) · [中文](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.zh-CN.md) · [العربية](https://github.com/caua-ferreira/permafrost-framework/blob/main/README.ar.md) · **हिन्दी**

[दस्तावेज़ीकरण](https://caua-ferreira.github.io/permafrost-framework) · [त्वरित प्रारंभ](#त्वरित-प्रारंभ) · [बेंचमार्क](#बेंचमार्क) · [API संदर्भ](#api-संदर्भ)

</div>

---

## Permafrost क्या है?

कॉर्पोरेट ऐतिहासिक डेटा — CSV, JSONL, डेटाबेस डंप — वर्षों तक उच्च लागत पर cold storage (S3 Glacier, Azure Archive) में रहता है। समस्या यह है: यदि आपको 10 GB फ़ाइल में केवल एक महीने का डेटा चाहिए, तो आपको **सब कुछ** डीकम्प्रेस करना पड़ता है।

Permafrost इसे दो तंत्रों से हल करता है:

1. **कॉलम प्रेडिक्टर** — संपीड़न से पहले डेटा को अर्थात्मक रूप से परिवर्तित करता है (delta, zigzag, timestamps, categories), जो सामान्य LZMA2 से कहीं बेहतर अनुपात प्राप्त करता है
2. **Sparse Index** — फ़ाइल में एम्बेड किया गया एक सूचकांक जो प्रत्येक chunk के सटीक byte offset को इंगित करता है, जिससे पूरी फ़ाइल डाउनलोड किए बिना HTTP Range Requests द्वारा चुनिंदा पढ़ाई संभव होती है

```
21,00,00,000 पंक्तियाँ × 13 कॉलम — स्थानीय रूप से मापा गया वास्तविक बेंचमार्क:

कच्चा CSV:               16.35 GB  (1.00×)
Parquet + Snappy:          5.89 GB  (2.78×)   लिखाई:  8.9 मिनट
CSV + सामान्य LZMA2 (p9): ~3.80 GB  (~4.3×)   लिखाई:  ~7 घंटे  ⚠️ अव्यावहारिक
Permafrost + ZSTD:         3.25 GB  (5.03×)   लिखाई: 77.7 मिनट
Permafrost + LZMA2:        3.03 GB  (5.40×)   लिखाई: 93.5 मिनट   ← Parquet से लगभग 2× बेहतर

केवल 2022 की क्वेरी → 5.7 मिनट में 4.2 करोड़ पंक्तियाँ — केवल 20% फ़ाइल पढ़ी गई
```

---

## विशेषताएँ

- **उच्च संपीड़न** — Zstd / LZMA2 / ZPAQ से पहले कॉलम प्रेडिक्टर (delta_zigzag, lag1_zigzag, ts_delta_s, category_u8)
- **चुनिंदा पठन** — एम्बेडेड sparse index `filter={"year": 2023}` को बाकी को डीकम्प्रेस किए बिना सक्षम करता है
- **अखंडता की गारंटी** — प्रत्येक chunk के लिए SHA-256, किसी भी डीकम्प्रेशन से पहले सत्यापित
- **स्व-वर्णनात्मक** — फ़ाइल में पूरा Arrow schema एम्बेड; बाहरी दस्तावेज़ के बिना 2040 में पठनीय
- **क्लाउड-नेटिव** — S3, Google Cloud Storage और Azure Blob Storage के लिए HTTP Range Requests के साथ मूल समर्थन
- **DuckDB कैटलॉग** — कुछ भी डाउनलोड किए बिना सैकड़ों रिमोट फ़ाइलों में मेटाडेटा खोज
- **स्ट्रीमिंग** — `freeze_file()` और `peek()` के साथ RAM से बड़े डेटासेट प्रोसेस करें
- **वितरित क्लस्टर** — FastAPI के माध्यम से Master + Workers; N workers के साथ 1 TB समानांतर प्रोसेसिंग
- **एन्क्रिप्शन** — प्रति chunk AES-256-GCM, 0.00% स्टोरेज ओवरहेड
- **पूर्ण CLI** — `permafrost freeze / unfreeze / audit / verify / catalog`

---

## स्थापना

```bash
# बुनियादी स्थापना
pip install permafrost-framework

# AWS S3 समर्थन के साथ
pip install "permafrost-framework[s3]"

# Google Cloud Storage समर्थन के साथ
pip install "permafrost-framework[gcs]"

# Azure Blob Storage समर्थन के साथ
pip install "permafrost-framework[azure]"

# सभी क्लाउड प्रदाता
pip install "permafrost-framework[all-cloud]"
```

**आवश्यकताएँ:** Python 3.10+

---

## त्वरित प्रारंभ

### बुनियादी Freeze और Unfreeze

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("sales_history.csv")

# संपीड़न — मेट्रिक्स लौटाता है
metrics = pf.freeze(df, "sales.permafrost", codec=pf.CODEC_LZMA2, partition_by="year")
print(f"Ratio: {metrics['ratio']:.2f}×  |  {metrics['original_mb']:.1f} MB → {metrics['stored_mb']:.1f} MB")

# सब कुछ डीकम्प्रेस करें
df_back = pf.unfreeze("sales.permafrost", verify=True)

# केवल 2023 डीकम्प्रेस करें — केवल उस वर्ष के chunk पढ़ता है
df_2023 = pf.unfreeze("sales.permafrost", filter={"year": 2023})
```

### स्ट्रीमिंग (RAM से बड़े डेटासेट)

```python
# मेमोरी में लोड किए बिना बड़ी फ़ाइल संपीड़ित करें
pf.freeze_file("100gb.csv", "output.permafrost", chunk_rows=50_000)

# बैचों में पुनरावृत्त पठन
for batch_df in pf.peek("output.permafrost", batch_size=50_000):
    process(batch_df)
```

### क्लाउड (S3, GCS, Azure)

```python
# सीधे S3 पर अपलोड करें
pf.freeze_to(df, "s3://my-bucket/data/sales.permafrost")

# S3 से चुनिंदा पठन — पूरी फ़ाइल डाउनलोड नहीं करता
df_2023 = pf.thaw_from("s3://my-bucket/data/sales.permafrost", filter={"year": 2023})
```

### CLI

```bash
# संपीड़न
permafrost freeze sales.csv sales.permafrost --codec lzma2 --partition-by year

# फ़िल्टर के साथ डीकम्प्रेशन
permafrost unfreeze sales.permafrost --filter '{"year": 2023}' --output sales_2023.csv

# ऑडिट (डीकम्प्रेस किए बिना)
permafrost audit sales.permafrost
```

---

## बेंचमार्क

### संपीड़न बनाम विकल्प

| प्रारूप | आकार | अनुपात | लिखाई का समय |
|---------|------|--------|-------------|
| कच्चा CSV | 16.35 GB | 1.00× | — |
| Parquet + Snappy | 5.89 GB | 2.78× | 8.9 मिनट |
| CSV + LZMA2 *(p9)* | ~3.80 GB | ~4.3× | **~7 घंटे** ⚠️ |
| **Permafrost + ZSTD** | **3.25 GB** | **5.03×** | **77.7 मिनट** |
| **Permafrost + LZMA2** | **3.03 GB** | **5.40×** | **93.5 मिनट** |

### क्लाउड स्टोरेज लागत (S3 Glacier Deep Archive)

| मूल डेटा | Permafrost के बिना | Permafrost के साथ (5.4×) | मासिक बचत |
|---------|-------------------|------------------------|----------|
| 1 TB | $0.99 | **$0.18** | **-81%** |
| 10 TB | $9.90 | **$1.83** | **-81%** |
| 100 TB | $99.00 | **$18.33** | **-81%** |

---

## API संदर्भ

| फ़ंक्शन | विवरण |
|---------|-------|
| `pf.freeze(df, path, ...)` | DataFrame को `.permafrost` फ़ाइल में संपीड़ित करें |
| `pf.unfreeze(path, filter=None)` | डीकम्प्रेस करें; `filter` sparse index का उपयोग करता है |
| `pf.audit(path)` | डीकम्प्रेस किए बिना मेटाडेटा लौटाता है |
| `pf.freeze_append(path, df_new)` | मौजूदा फ़ाइल में पंक्तियाँ जोड़ें |
| `pf.peek(path, batch_size=50_000)` | पुनरावृत्त बैचों में डीकम्प्रेस करें |
| `pf.freeze_to(df, uri)` | क्लाउड पर सीधे संपीड़ित और अपलोड करें |
| `pf.thaw_from(uri, filter=None)` | Range Request से क्लाउड से डीकम्प्रेस करें |

---

## योगदान

योगदान का स्वागत है! [योगदान गाइड](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md) देखें।

```bash
git clone https://github.com/caua-ferreira/permafrost-framework
cd permafrost-framework
pip install -e ".[dev]"
pytest tests/ -v
```

---

## लाइसेंस

Apache License 2.0 — [LICENSE](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE) देखें।

---

<div align="center">

❄️ के साथ बनाया गया, उन डेटा के लिए जिन्हें दशकों तक टिकना है।

</div>

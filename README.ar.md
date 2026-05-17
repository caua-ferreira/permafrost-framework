<div dir="rtl">

# ❄️ Permafrost Framework

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/permafrost-framework?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/permafrost-framework/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/permafrost-framework?color=blue)](https://pypi.org/project/permafrost-framework/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/permafrost-framework/)
[![Tests](https://img.shields.io/github/actions/workflow/status/caua-ferreira/permafrost-framework/tests.yml?label=tests&logo=github)](https://github.com/caua-ferreira/permafrost-framework/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE)

**منصة ضغط ذكية للأرشفة الرقمية طويلة المدى.**

*٢١٠ مليون صف: Permafrost + LZMA2 = ٣.٠٣ جيجابايت مقابل CSV = ١٦.٣٥ جيجابايت (٥.٤×) — أفضل بما يقارب ضعفين من Parquet. استعلم عن عام واحد من بيانات خمس سنوات: قراءة ٤٢ مليون صف، مع الوصول إلى ٢٠٪ فقط من الملف.*

🌐 [English](README.md) · [Português (BR)](README.pt-BR.md) · [Español](README.es.md) · [Français](README.fr.md) · [中文](README.zh-CN.md) · **العربية** · [हिन्दी](README.hi.md)

[التوثيق](https://caua-ferreira.github.io/permafrost-framework) · [البدء السريع](#البدء-السريع) · [المعايير](#المعايير) · [مرجع-API](#مرجع-api)

</div>

---

## ما هو Permafrost؟

البيانات التاريخية للشركات — ملفات CSV وJSONL وتصدير قواعد البيانات — تُخزَّن في التخزين البارد (S3 Glacier، Azure Archive) لسنوات بتكاليف عالية. المشكلة: إذا احتجت إلى بيانات شهر واحد في ملف حجمه ١٠ جيجابايت، يجب عليك فك ضغط **كل شيء**.

يحلّ Permafrost هذه المشكلة بآليتين:

1. **متنبئات الأعمدة** — تُحوّل البيانات دلالياً قبل الضغط (delta، zigzag، الطوابع الزمنية، الفئات)، لتحقيق نسب ضغط أعلى بكثير من LZMA2 النقي
2. **الفهرس المتناثر (Sparse Index)** — فهرس مضمّن في الملف يُشير إلى الإزاحة البايتية الدقيقة لكل قطعة بيانات، مما يُتيح القراءة الانتقائية عبر HTTP Range Requests دون تنزيل الملف بالكامل

</div>

```
210,000,000 صف × 13 عمود — معايير حقيقية مقاسة محلياً:

CSV خام:                16.35 GB  (1.00×)
Parquet + Snappy:        5.89 GB  (2.78×)   كتابة:  8.9 دقيقة
CSV + LZMA2 نقي (p9):  ~3.80 GB  (~4.3×)   كتابة:  ~7 ساعات  ⚠️ غير عملي
Permafrost + ZSTD:       3.25 GB  (5.03×)   كتابة: 77.7 دقيقة
Permafrost + LZMA2:      3.03 GB  (5.40×)   كتابة: 93.5 دقيقة

قراءة عام 2022 فقط → 42 مليون صف في 5.7 دقيقة — 20% من الملف فقط، 80% لم يُمسّ
```

<div dir="rtl">

---

## المميزات

- **ضغط عالٍ** — متنبئات أعمدة (delta_zigzag، lag1_zigzag، ts_delta_s، category_u8) قبل Zstd / LZMA2 / ZPAQ
- **قراءات انتقائية** — الفهرس المتناثر المضمّن يُتيح `filter={"year": 2023}` دون فك ضغط الباقي
- **سلامة مضمونة** — SHA-256 لكل قطعة بيانات، يُتحقق منها قبل أي فك ضغط
- **وصف ذاتي** — مخطط Arrow الكامل مضمّن في الملف؛ قابل للقراءة عام ٢٠٤٠ دون وثائق خارجية
- **متوافق مع السحابة** — دعم أصلي لـ S3 وGoogle Cloud Storage وAzure Blob Storage مع HTTP Range Requests
- **فهرس DuckDB** — البحث في البيانات الوصفية لمئات الملفات البعيدة دون تنزيل أي منها
- **البث (Streaming)** — معالجة مجموعات البيانات الأكبر من الذاكرة العشوائية
- **مجموعة موزّعة** — Master + Workers عبر FastAPI؛ معالجة ١ تيرابايت بالتوازي
- **تشفير** — AES-256-GCM لكل قطعة، مع نفقات تخزين ٠.٠٠٪

---

## التثبيت

</div>

```bash
# التثبيت الأساسي
pip install permafrost-framework

# مع دعم AWS S3
pip install "permafrost-framework[s3]"

# مع دعم Google Cloud Storage
pip install "permafrost-framework[gcs]"

# مع دعم Azure Blob Storage
pip install "permafrost-framework[azure]"

# جميع مزودي السحابة
pip install "permafrost-framework[all-cloud]"
```

<div dir="rtl">

**المتطلبات:** Python 3.10+

---

## البدء السريع

### الضغط وفك الضغط الأساسي

</div>

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("sales_history.csv")

# الضغط — يعيد مقاييس الأداء
metrics = pf.freeze(df, "sales.permafrost", codec=pf.CODEC_LZMA2, partition_by="year")
print(f"Ratio: {metrics['ratio']:.2f}×  |  {metrics['original_mb']:.1f} MB → {metrics['stored_mb']:.1f} MB")

# فك الضغط الكامل
df_back = pf.unfreeze("sales.permafrost", verify=True)

# فك ضغط عام 2023 فقط — يقرأ قطع ذلك العام فقط
df_2023 = pf.unfreeze("sales.permafrost", filter={"year": 2023})
```

```python
# ضغط ملف كبير دون تحميله في الذاكرة
pf.freeze_file("100gb.csv", "output.permafrost", chunk_rows=50_000)

# القراءة دفعة دفعة
for batch_df in pf.peek("output.permafrost", batch_size=50_000):
    process(batch_df)
```

```bash
# الضغط
permafrost freeze sales.csv sales.permafrost --codec lzma2 --partition-by year

# فك الضغط مع تصفية
permafrost unfreeze sales.permafrost --filter '{"year": 2023}' --output sales_2023.csv

# التدقيق (دون فك الضغط)
permafrost audit sales.permafrost
```

<div dir="rtl">

---

## المعايير

### الضغط مقارنةً بالبدائل

| الصيغة | الحجم | النسبة | وقت الكتابة |
|--------|-------|--------|------------|
| CSV خام | 16.35 GB | 1.00× | — |
| Parquet + Snappy | 5.89 GB | 2.78× | 8.9 دقيقة |
| CSV + LZMA2 نقي *(p9)* | ~3.80 GB | ~4.3× | **~7 ساعات** ⚠️ |
| **Permafrost + ZSTD** | **3.25 GB** | **5.03×** | **77.7 دقيقة** |
| **Permafrost + LZMA2** | **3.03 GB** | **5.40×** | **93.5 دقيقة** |

### تكلفة التخزين السحابي (S3 Glacier Deep Archive)

| الحجم الأصلي | بدون Permafrost | مع Permafrost (5.4×) | الوفورات الشهرية |
|-------------|-----------------|---------------------|-----------------|
| 1 TB | $0.99 | **$0.18** | **-81%** |
| 10 TB | $9.90 | **$1.83** | **-81%** |
| 100 TB | $99.00 | **$18.33** | **-81%** |

---

## مرجع API

| الدالة | الوصف |
|--------|-------|
| `pf.freeze(df, path, ...)` | ضغط DataFrame إلى ملف `.permafrost` |
| `pf.unfreeze(path, filter=None)` | فك الضغط؛ `filter` يستخدم الفهرس المتناثر |
| `pf.audit(path)` | إرجاع البيانات الوصفية دون فك الضغط |
| `pf.freeze_append(path, df_new)` | إضافة صفوف إلى ملف موجود دون إعادة الضغط |
| `pf.peek(path, batch_size=50_000)` | فك الضغط على دفعات متكررة |
| `pf.freeze_to(df, uri)` | الضغط والرفع مباشرةً إلى السحابة |
| `pf.thaw_from(uri, filter=None)` | فك الضغط من السحابة باستخدام Range Request |

---

## المساهمة

المساهمات مرحب بها! راجع [دليل المساهمة](https://github.com/caua-ferreira/permafrost-framework/blob/main/CONTRIBUTING.md).

---

## الرخصة

Apache License 2.0 — انظر [LICENSE](https://github.com/caua-ferreira/permafrost-framework/blob/main/LICENSE).

---

<div align="center">

صُنع بـ ❄️ للبيانات التي تحتاج إلى الدوام عقوداً.

</div>

</div>

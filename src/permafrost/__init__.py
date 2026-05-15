"""
Permafrost Data Framework
=========================
Plataforma distribuída de compressão inteligente para arquivamento digital de longo prazo.

Uso rápido:
    from permafrost import freeze, thaw, audit
    from permafrost import PermafrostCatalog, SchemaDetector
    from permafrost import freeze_to, thaw_from          # cloud
    from permafrost import PermafrostMaster, PermafrostWorker, PermafrostClient  # cluster

Formatos suportados:
    freeze(df, "arquivo.permafrost")                     # DataFrame tabular
    freeze(detector.detect("dados.jsonl")[0], ...)       # JSONL / NoSQL
    freeze_file("dados.csv", "saida.permafrost")         # streaming, sem carregar tudo na RAM
    freeze_to(df, "s3://bucket/dados.permafrost")        # direto para cloud

Exemplos:
    >>> import permafrost as pf
    >>> metrics = pf.freeze(df, "vendas.permafrost", codec=pf.CODEC_LZMA2)
    >>> print(f"Ratio: {metrics['ratio']:.2f}x")
    >>> df_back = pf.thaw("vendas.permafrost")
    >>> info = pf.audit("vendas.permafrost")   # sem descomprimir

Links:
    GitHub: https://github.com/caua-ferreira/permafrost-framework
    Docs:   https://github.com/caua-ferreira/permafrost-framework/tree/main/docs
"""

__version__  = "0.6.4"
__author__   = "Permafrost Contributors"
__license__  = "Apache-2.0"

# ── Core codec ────────────────────────────────────────────────────────────────
from permafrost.codec import (
    freeze,
    thaw,
    audit,
    # Codec IDs
    CODEC_ZSTD,
    CODEC_LZMA2,
    CODEC_ZPAQ,
    # Quantization levels
    QUANT_NONE,
    QUANT_HIGH,
    QUANT_MEDIUM,
    QUANT_LOW,
    # Predictor names
    PRED_DELTA,
    PRED_LAG1,
    PRED_CATEGORY,
    PRED_TS,
    PRED_RAW,
    PRED_FLOAT32,
    PRED_FLOAT16,
    # Format constants
    MAGIC,
    EOF_MAGIC,
)

# ── Schema detection (SQL + NoSQL + JSONL) ────────────────────────────────────
from permafrost.schema_detector import (
    SchemaDetector,
    DataType,
    FieldKind,
)

# ── Chunk mode (streaming — datasets > RAM) ───────────────────────────────────
from permafrost.chunk_mode import (
    freeze_stream,
    freeze_file,
    thaw_iter,
)

# ── Encryption ────────────────────────────────────────────────────────────────
from permafrost.crypto import KeyProvider, LocalKeyProvider

# ── Schema evolution ──────────────────────────────────────────────────────────
from permafrost.schema_evolution import (
    SchemaEvolutionError,
    apply_schema_evolution,
    schema_diff,
)

# ── Catalog (DuckDB index) ────────────────────────────────────────────────────
from permafrost.catalog import PermafrostCatalog

# ── Cloud storage adapters ────────────────────────────────────────────────────
from permafrost.storage import (
    LocalAdapter,
    S3Adapter,
    GCSAdapter,
    AzureAdapter,
    storage_from_uri,
    parse_uri,
    freeze_to,
    thaw_from,
    audit_remote,
    ResumableUploadError,
)

# ── Cluster (distributed processing) ─────────────────────────────────────────
from permafrost.cluster import (
    PermafrostMaster,
    PermafrostWorker,
    PermafrostClient,
)

__all__ = [
    # Core
    "freeze", "thaw", "audit",
    # Codecs
    "CODEC_ZSTD", "CODEC_LZMA2", "CODEC_ZPAQ",
    # Quant levels
    "QUANT_NONE", "QUANT_HIGH", "QUANT_MEDIUM", "QUANT_LOW",
    # Predictors
    "PRED_DELTA", "PRED_LAG1", "PRED_CATEGORY", "PRED_TS", "PRED_RAW",
    "PRED_FLOAT32", "PRED_FLOAT16",
    # Schema
    "SchemaDetector", "DataType", "FieldKind",
    # Chunk mode
    "freeze_stream", "freeze_file", "thaw_iter",
    # Catalog
    "PermafrostCatalog",
    # Storage
    "LocalAdapter", "S3Adapter", "GCSAdapter", "AzureAdapter",
    "storage_from_uri", "parse_uri", "freeze_to", "thaw_from", "audit_remote",
    "ResumableUploadError",
    # Cluster
    "PermafrostMaster", "PermafrostWorker", "PermafrostClient",
    # Encryption
    "KeyProvider", "LocalKeyProvider",
    # Schema evolution
    "SchemaEvolutionError", "apply_schema_evolution", "schema_diff",
]
# ── Spark DataSource API v2 ───────────────────────────────────────────────────
try:
    from permafrost.spark import PermafrostDataSource, register as spark_register
    __all__ += ["PermafrostDataSource", "spark_register"]
except ImportError:
    pass   # PySpark não instalado — ok

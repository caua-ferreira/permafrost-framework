"""Permafrost Data Framework v0.4.0"""
from .permafrost_codec import (
    freeze, thaw, audit,
    CODEC_ZSTD, CODEC_LZMA2, CODEC_ZPAQ,
    QUANT_NONE, QUANT_HIGH, QUANT_MEDIUM, QUANT_LOW,
)
from .permafrost_catalog import PermafrostCatalog
from .permafrost_schema_detector import SchemaDetector, DataType
from .permafrost_chunk_mode import freeze_stream, freeze_file, thaw_iter
from .permafrost_storage import (
    LocalAdapter, S3Adapter, GCSAdapter, AzureAdapter,
    storage_from_uri, freeze_to, thaw_from, audit_remote, parse_uri,
)

__version__ = "0.4.0"
__all__ = [
    "freeze","thaw","audit",
    "freeze_stream","freeze_file","thaw_iter",
    "freeze_to","thaw_from","audit_remote",
    "PermafrostCatalog","SchemaDetector","DataType",
    "LocalAdapter","S3Adapter","GCSAdapter","AzureAdapter",
    "storage_from_uri","parse_uri",
    "CODEC_ZSTD","CODEC_LZMA2","CODEC_ZPAQ",
    "QUANT_NONE","QUANT_HIGH","QUANT_MEDIUM","QUANT_LOW",
]

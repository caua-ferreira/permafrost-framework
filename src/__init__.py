"""Permafrost Data Framework v0.3.0"""
from .permafrost_codec import (
    freeze, thaw, audit,
    CODEC_ZSTD, CODEC_LZMA2, CODEC_ZPAQ,
    QUANT_NONE, QUANT_HIGH, QUANT_MEDIUM, QUANT_LOW,
)
from .permafrost_catalog import PermafrostCatalog
from .permafrost_schema_detector import SchemaDetector, DataType
from .permafrost_chunk_mode import freeze_stream, freeze_file, thaw_iter

__version__ = "0.3.0"
__all__ = [
    "freeze","thaw","audit","PermafrostCatalog",
    "SchemaDetector","DataType",
    "freeze_stream","freeze_file","thaw_iter",
    "CODEC_ZSTD","CODEC_LZMA2","CODEC_ZPAQ",
    "QUANT_NONE","QUANT_HIGH","QUANT_MEDIUM","QUANT_LOW",
]

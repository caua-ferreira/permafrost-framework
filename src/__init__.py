"""
Permafrost Data Framework v0.2
Formato .permafrost — compressão extrema para arquivamento de longo prazo
"""
from .permafrost_codec_v3 import (
    freeze, thaw, audit,
    CODEC_ZSTD, CODEC_LZMA2, CODEC_ZPAQ,
    QUANT_NONE, QUANT_HIGH, QUANT_MEDIUM, QUANT_LOW,
    MAGIC, EOF_MAGIC,
)
from .permafrost_catalog import PermafrostCatalog

__version__ = "0.2.0"
__all__ = [
    "freeze", "thaw", "audit",
    "PermafrostCatalog",
    "CODEC_ZSTD", "CODEC_LZMA2", "CODEC_ZPAQ",
    "QUANT_NONE", "QUANT_HIGH", "QUANT_MEDIUM", "QUANT_LOW",
]

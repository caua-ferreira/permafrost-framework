"""
Permafrost Data Framework
Formato .permafrost v1.0 — compressão extrema para arquivamento de longo prazo
"""
from .permafrost_codec import (
    freeze, thaw, audit,
    CODEC_ZSTD, CODEC_LZMA2, CODEC_ZPAQ,
    QUANT_NONE, QUANT_HIGH, QUANT_MEDIUM, QUANT_LOW,
    MAGIC, EOF_MAGIC, VERSION,
)

__version__ = "0.1.0"
__all__ = [
    "freeze", "thaw", "audit",
    "CODEC_ZSTD", "CODEC_LZMA2", "CODEC_ZPAQ",
    "QUANT_NONE", "QUANT_HIGH", "QUANT_MEDIUM", "QUANT_LOW",
]

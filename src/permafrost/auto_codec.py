"""
Auto Codec Selector — I2 feature (v0.8)

Analisa um sample do DataFrame e escolhe codec + quant usando heurísticas
calibradas em benchmarks internos de compressão (sem dependência externa).

Regras de decisão (imitam o que uma decision tree aprenderia):
  LZMA2  — favorecido por strings de baixa cardinalidade, timestamps,
            inteiros sequenciais e floats de baixa variância.
  ZSTD   — favorecido por floats de alta variância, arquivos grandes (>200 MB)
            e perfis mistos sem padrão dominante.
  QUANT_HIGH (float32)  — sugerido apenas quando floats dominam (>60% das
            colunas) e a variância indica valores bounded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from permafrost.codec import (
    CODEC_LZMA2, CODEC_ZSTD,
    QUANT_NONE, QUANT_HIGH,
)

CODEC_AUTO = "auto"


# ── Perfil de dados ───────────────────────────────────────────────────────────

@dataclass
class DataProfile:
    """Features extraídas de um sample do DataFrame para guiar a seleção."""
    n_rows:               int
    n_cols:               int
    float_col_ratio:      float   # colunas float / total
    int_col_ratio:        float   # colunas inteiras / total
    str_col_ratio:        float   # colunas string/object / total
    ts_col_ratio:         float   # colunas datetime / total
    float_cv_mean:        float   # média do coef. de variação das colunas float
    str_cardinality_mean: float   # média de (únicos / total) para colunas str
    estimated_mb:         float   # RAM estimada do DataFrame original


def _col_kind(series: pd.Series) -> str:
    if pd.api.types.is_float_dtype(series):
        return "float"
    if pd.api.types.is_integer_dtype(series):
        return "int"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "ts"
    return "str"


def profile_dataframe(df: pd.DataFrame, sample_size: int = 1000) -> DataProfile:
    """Extrai features de compressão de um sample do DataFrame.

    Args:
        df: DataFrame a analisar.
        sample_size: Número máximo de linhas amostradas (padrão: 1000).

    Returns:
        :class:`DataProfile` com as features extraídas.
    """
    n_rows = min(len(df), sample_size)
    n_cols = len(df.columns)

    if n_cols == 0 or n_rows == 0:
        return DataProfile(
            n_rows=n_rows, n_cols=n_cols,
            float_col_ratio=0.0, int_col_ratio=0.0,
            str_col_ratio=0.0, ts_col_ratio=0.0,
            float_cv_mean=0.0, str_cardinality_mean=0.0,
            estimated_mb=0.0,
        )

    sample = df.head(n_rows)
    counts   = {"float": 0, "int": 0, "str": 0, "ts": 0}
    float_cvs: list[float] = []
    str_cards: list[float] = []

    for col in sample.columns:
        kind = _col_kind(sample[col])
        counts[kind] += 1

        if kind == "float":
            s = pd.to_numeric(sample[col], errors="coerce").dropna()
            if len(s) > 1:
                mean_abs = np.abs(s).mean()
                cv = float(s.std() / (mean_abs + 1e-9))
                float_cvs.append(min(cv, 10.0))   # cap para evitar outliers

        elif kind == "str":
            s = sample[col].dropna()
            if len(s) > 0:
                str_cards.append(float(s.nunique() / len(s)))

    return DataProfile(
        n_rows=n_rows,
        n_cols=n_cols,
        float_col_ratio=counts["float"] / n_cols,
        int_col_ratio=counts["int"]   / n_cols,
        str_col_ratio=counts["str"]   / n_cols,
        ts_col_ratio=counts["ts"]    / n_cols,
        float_cv_mean=float(np.mean(float_cvs)) if float_cvs else 0.0,
        str_cardinality_mean=float(np.mean(str_cards)) if str_cards else 0.0,
        estimated_mb=round(df.memory_usage(deep=True).sum() / 1e6, 3),
    )


# ── Seleção automática ────────────────────────────────────────────────────────

def auto_select(df: pd.DataFrame, sample_size: int = 1000) -> dict:
    """Seleciona o melhor codec e nível de quantização para o DataFrame.

    Usa heurísticas calibradas em benchmarks internos.  Não treina nenhum
    modelo em tempo de execução — as regras são determinísticas e estáveis.

    Args:
        df: DataFrame a analisar.
        sample_size: Linhas amostradas para o perfil (padrão: 1000).

    Returns:
        Dicionário com as chaves:

        - ``codec``: :data:`CODEC_LZMA2` ou :data:`CODEC_ZSTD`
        - ``quant``: :data:`QUANT_NONE` ou :data:`QUANT_HIGH`
        - ``reason``: string explicando a decisão
        - ``profile``: instância de :class:`DataProfile`

    Raises:
        TypeError: Se ``df`` não for um ``pd.DataFrame``.

    Example::

        result = auto_select(df)
        pf.freeze(df, "saida.permafrost",
                  codec=result['codec'], quant=result['quant'])
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"auto_select requer pd.DataFrame, recebeu {type(df).__name__}")

    if len(df) == 0 or len(df.columns) == 0:
        return {
            "codec":   CODEC_ZSTD,
            "quant":   QUANT_NONE,
            "reason":  "DataFrame vazio — ZSTD lossless padrão",
            "profile": None,
        }

    p = profile_dataframe(df, sample_size=sample_size)
    reasons: list[str] = []
    lzma2_score = 0.0

    # ── Sinais que favorecem LZMA2 ────────────────────────────────────────────

    if p.str_col_ratio > 0:
        if p.str_cardinality_mean < 0.05:
            lzma2_score += 2.5
            reasons.append("strings de baixa cardinalidade (<5% únicos)")
        elif p.str_cardinality_mean < 0.20:
            lzma2_score += 1.0
            reasons.append("strings moderadamente repetitivas")

    if p.ts_col_ratio > 0.10:
        lzma2_score += 1.5
        reasons.append("colunas de timestamp (ts_delta eficaz)")

    if p.int_col_ratio > 0.50:
        lzma2_score += 2.0
        reasons.append("maioria de colunas inteiras (delta_zigzag muito eficaz)")
    elif p.int_col_ratio > 0.30:
        lzma2_score += 1.0
        reasons.append("colunas inteiras (delta_zigzag eficaz)")

    if p.float_col_ratio > 0:
        if p.float_cv_mean < 0.10:
            lzma2_score += 2.0
            reasons.append("floats de variância muito baixa (lag1 muito eficaz)")
        elif p.float_cv_mean < 0.5:
            lzma2_score += 1.0
            reasons.append("floats de baixa variância (lag1 eficaz)")

    # ── Penalidades para LZMA2 ────────────────────────────────────────────────

    if p.estimated_mb > 200:
        lzma2_score -= 2.0
        reasons.append(f"arquivo grande ({p.estimated_mb:.0f} MB — ZSTD é 6× mais rápido)")
    elif p.estimated_mb > 50:
        lzma2_score -= 1.0

    if p.float_col_ratio > 0.5 and p.float_cv_mean > 2.0:
        lzma2_score -= 1.5
        reasons.append("floats de alta variância (compressores equivalentes)")

    # ── Decisão de codec ──────────────────────────────────────────────────────

    codec = CODEC_LZMA2 if lzma2_score >= 1.5 else CODEC_ZSTD

    # ── Decisão de quantização ────────────────────────────────────────────────
    # Conservador: só sugere float32 se floats dominam E variância é moderada
    # (evita overflow/perda de precisão não intencional)

    quant = QUANT_NONE
    if p.float_col_ratio > 0.6 and p.float_cv_mean < 3.0 and codec == CODEC_ZSTD:
        quant = QUANT_HIGH
        reasons.append("floats dominantes com variância moderada → float32")

    # ── Mensagem final ────────────────────────────────────────────────────────

    if not reasons:
        reasons.append("perfil neutro — ZSTD lossless padrão")

    codec_name = "LZMA2" if codec == CODEC_LZMA2 else "ZSTD"
    quant_name = "float32" if quant == QUANT_HIGH else "lossless"
    reason_str = f"{codec_name}+{quant_name}: {'; '.join(reasons)}"

    return {
        "codec":   codec,
        "quant":   quant,
        "reason":  reason_str,
        "profile": p,
    }

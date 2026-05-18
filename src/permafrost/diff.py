"""
permafrost.diff — Comparação entre versões de arquivos .permafrost/.pf
=======================================================================

Detecta linhas inseridas, removidas e alteradas entre dois arquivos com o
mesmo schema. Requer uma chave primária para identificar linhas — seja
gravada no arquivo via ``freeze(primary_key=...)``, passada como parâmetro
``on=``, ou inferida pelo índice posicional.

Uso rápido::

    import permafrost as pf

    result = pf.diff("v1.permafrost", "v2.permafrost")
    print(result["summary"])
    # {"inserted": 120, "deleted": 45, "changed": 300, "unchanged": 9535}

    # Só o resumo (sem carregar linhas)
    s = pf.diff("v1.permafrost", "v2.permafrost", output="summary")

    # DataFrame unificado com coluna _diff
    df = pf.diff("v1.permafrost", "v2.permafrost", output="dataframe")
"""

from __future__ import annotations

import math
import os
import warnings
from typing import Any, Optional, Union

import numpy as np
import pandas as pd

from permafrost.codec import unfreeze, _read_header, PERMAFROST_EXTENSIONS


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_primary_key(path: str) -> Optional[list[str]]:
    """Read primary_key stored in the file manifest, or None."""
    with open(path, "rb") as f:
        raw = f.read()
    h = _read_header(raw)
    return h.get("primary_key")


def _validate_schema(df1: pd.DataFrame, df2: pd.DataFrame) -> None:
    c1, c2 = set(df1.columns), set(df2.columns)
    if c1 != c2:
        extra   = c2 - c1
        missing = c1 - c2
        parts = []
        if extra:   parts.append(f"colunas extras em v2: {sorted(extra)}")
        if missing: parts.append(f"colunas ausentes em v2: {sorted(missing)}")
        raise ValueError("Schema incompatível — " + "; ".join(parts))


def _values_equal(s1: pd.Series, s2: pd.Series, rtol: float = 1e-9) -> pd.Series:
    """Element-wise equality, with float tolerance and NaN == NaN."""
    if pd.api.types.is_float_dtype(s1) or pd.api.types.is_float_dtype(s2):
        both_nan = s1.isna() & s2.isna()
        close    = np.isclose(s1.fillna(0), s2.fillna(0), rtol=rtol, equal_nan=False)
        return both_nan | close
    # For non-float: treat NaN == NaN
    both_nan = s1.isna() & s2.isna()
    return both_nan | (s1 == s2)


def _rows_equal(df_v1: pd.DataFrame, df_v2: pd.DataFrame,
                value_cols: list[str], rtol: float = 1e-9) -> pd.Series:
    """Returns boolean Series: True when all value_cols are equal row-by-row."""
    eq = pd.Series(True, index=df_v1.index)
    for col in value_cols:
        eq = eq & _values_equal(df_v1[col].reset_index(drop=True),
                                df_v2[col].reset_index(drop=True), rtol)
    return eq


# ── main function ─────────────────────────────────────────────────────────────

def diff(
    path_v1: str,
    path_v2: str,
    on: Union[str, list[str], None] = None,
    output: str = "dict",
    include: Optional[list[str]] = None,
    changed_columns_only: bool = False,
    rtol: float = 1e-9,
    key=None,
    verify: bool = True,
) -> Any:
    """Compare two .permafrost/.pf files and return what changed.

    Args:
        path_v1: Path to the older/base file.
        path_v2: Path to the newer file.
        on: Column name(s) used to match rows across versions (primary key for
            the diff). If ``None``, tries ``primary_key`` from the file manifest,
            then falls back to positional index with a warning.
        output: Return format:
            ``"dict"`` (default) — ``{"inserted": df, "deleted": df, "changed": df,
            "unchanged_count": int, "summary": dict}``;
            ``"dataframe"`` — single DataFrame with ``_diff`` column
            (``"inserted"``, ``"deleted"``, ``"changed"``);
            ``"summary"`` — only counts, no DataFrames loaded.
        include: Subset of diff types to return. Any combination of
            ``["inserted", "deleted", "changed"]``. Default: all three.
        changed_columns_only: When ``True``, the ``changed`` DataFrame includes
            only columns that actually differ (plus key columns).
        rtol: Relative tolerance for float comparisons (default ``1e-9``).
        key: Decryption key for encrypted files.
        verify: Validate SHA-256 of each chunk (default ``True``).

    Returns:
        Depends on ``output`` — see above.

    Raises:
        ValueError: If schemas are incompatible.
        ValueError: If ``on`` column is not found in either file.

    Examples:
        Summary only (fast — no row data loaded after merge)::

            counts = pf.diff("v1.permafrost", "v2.permafrost", output="summary")

        Unified DataFrame::

            df = pf.diff("v1.permafrost", "v2.permafrost", output="dataframe")
            print(df[df["_diff"] == "inserted"])

        Only inserted rows::

            r = pf.diff("v1.permafrost", "v2.permafrost", include=["inserted"])
            print(r["inserted"])
    """
    if include is None:
        include = ["inserted", "deleted", "changed"]

    output = output.lower()
    if output not in ("dict", "dataframe", "summary"):
        raise ValueError(
            f"output deve ser 'dict', 'dataframe' ou 'summary', recebido: {output!r}"
        )

    # ── Load both files ───────────────────────────────────────────────────────
    df1 = unfreeze(path_v1, verify=verify, key=key)
    df2 = unfreeze(path_v2, verify=verify, key=key)

    _validate_schema(df1, df2)

    all_cols = list(df1.columns)

    # ── Resolve join key ──────────────────────────────────────────────────────
    if on is not None:
        key_cols = [on] if isinstance(on, str) else list(on)
    else:
        pk1 = _get_primary_key(path_v1)
        pk2 = _get_primary_key(path_v2)
        key_cols = pk1 or pk2 or None

    positional = key_cols is None
    if positional:
        warnings.warn(
            "pf.diff(): nenhuma primary_key encontrada — usando índice posicional. "
            "Use on= ou freeze(primary_key=...) para diff correto.",
            UserWarning, stacklevel=2,
        )

    # ── Key-based diff ────────────────────────────────────────────────────────
    if not positional:
        for col in key_cols:
            if col not in df1.columns:
                raise ValueError(f"Coluna de chave '{col}' não encontrada em {path_v1!r}")
            if col not in df2.columns:
                raise ValueError(f"Coluna de chave '{col}' não encontrada em {path_v2!r}")

        value_cols = [c for c in all_cols if c not in key_cols]

        # Merge outer
        merged = df1.merge(df2, on=key_cols, how="outer",
                           suffixes=("__v1", "__v2"), indicator=True)

        mask_ins = merged["_merge"] == "right_only"
        mask_del = merged["_merge"] == "left_only"
        mask_both = merged["_merge"] == "both"

        # For "both" rows, check which value_cols changed
        if mask_both.any() and value_cols:
            eq = pd.Series(True, index=merged.index)
            for col in value_cols:
                c1 = merged[col + "__v1"]
                c2 = merged[col + "__v2"]
                eq = eq & _values_equal(c1, c2, rtol)
            mask_changed   = mask_both & ~eq
            mask_unchanged = mask_both &  eq
        else:
            mask_changed   = pd.Series(False, index=merged.index)
            mask_unchanged = mask_both

        n_unchanged = int(mask_unchanged.sum())

        summary = {
            "inserted":  int(mask_ins.sum()),
            "deleted":   int(mask_del.sum()),
            "changed":   int(mask_changed.sum()),
            "unchanged": n_unchanged,
        }

        if output == "summary":
            return summary

        # ── Build result DataFrames ───────────────────────────────────────────

        def _reconstruct_v2(mask: pd.DataFrame) -> pd.DataFrame:
            """Reconstruct v2-side columns (inserted or changed new values)."""
            rows = merged[mask].copy()
            result: dict = {}
            for col in key_cols:
                result[col] = rows[col].values
            for col in value_cols:
                result[col] = rows[col + "__v2"].values
            return pd.DataFrame(result)

        def _reconstruct_v1(mask: pd.DataFrame) -> pd.DataFrame:
            """Reconstruct v1-side columns (deleted rows)."""
            rows = merged[mask].copy()
            result: dict = {}
            for col in key_cols:
                result[col] = rows[col].values
            for col in value_cols:
                result[col] = rows[col + "__v1"].values
            return pd.DataFrame(result)

        def _reconstruct_changed(mask: pd.DataFrame) -> pd.DataFrame:
            """Changed rows: include both _v1 and _v2 per value column."""
            rows = merged[mask].copy()
            if changed_columns_only:
                # Only columns that differ in at least one changed row
                differ_cols = []
                for col in value_cols:
                    c1 = rows[col + "__v1"]
                    c2 = rows[col + "__v2"]
                    if not _values_equal(c1, c2, rtol).all():
                        differ_cols.append(col)
            else:
                differ_cols = value_cols
            result: dict = {}
            for col in key_cols:
                result[col] = rows[col].values
            for col in differ_cols:
                result[col + "_v1"] = rows[col + "__v1"].values
                result[col + "_v2"] = rows[col + "__v2"].values
            return pd.DataFrame(result)

        inserted  = _reconstruct_v2(mask_ins)  if "inserted"  in include else None
        deleted   = _reconstruct_v1(mask_del)  if "deleted"   in include else None
        changed   = _reconstruct_changed(mask_changed) if "changed" in include else None

    # ── Positional diff ───────────────────────────────────────────────────────
    else:
        n_common = min(len(df1), len(df2))
        df1_c = df1.iloc[:n_common].reset_index(drop=True)
        df2_c = df2.iloc[:n_common].reset_index(drop=True)

        if df1_c.shape[0] > 0:
            eq = _rows_equal(df1_c, df2_c, list(df1_c.columns), rtol)
        else:
            eq = pd.Series(dtype=bool)

        mask_changed = ~eq

        changed_df  = df2_c[mask_changed].copy() if "changed" in include else None
        inserted_df = df2.iloc[n_common:].reset_index(drop=True) if "inserted" in include else None
        deleted_df  = df1.iloc[n_common:].reset_index(drop=True) if "deleted"  in include else None

        n_unchanged = int((~mask_changed).sum())
        summary = {
            "inserted":  max(0, len(df2) - n_common),
            "deleted":   max(0, len(df1) - n_common),
            "changed":   int(mask_changed.sum()),
            "unchanged": n_unchanged,
        }

        if output == "summary":
            return summary

        inserted = inserted_df
        deleted  = deleted_df
        changed  = changed_df

    # ── Format output ─────────────────────────────────────────────────────────

    if output == "dict":
        return {
            "inserted":       inserted,
            "deleted":        deleted,
            "changed":        changed,
            "unchanged_count": summary["unchanged"],
            "summary":        summary,
        }

    # output == "dataframe"
    frames = []
    for label, df_part in [("inserted", inserted), ("deleted", deleted), ("changed", changed)]:
        if df_part is not None and len(df_part) > 0:
            df_part = df_part.copy()
            # For changed rows in key-based diff, flatten _v1/_v2 to just _v2 values
            if label == "changed" and not positional:
                flat: dict = {}
                for col in df_part.columns:
                    if col.endswith("_v2"):
                        flat[col[:-3]] = df_part[col].values
                    elif col.endswith("_v1"):
                        pass  # drop _v1 in unified output
                    else:
                        flat[col] = df_part[col].values
                df_part = pd.DataFrame(flat)
            df_part.insert(0, "_diff", label)
            frames.append(df_part)

    if not frames:
        return pd.DataFrame(columns=["_diff"] + all_cols)
    return pd.concat(frames, ignore_index=True)

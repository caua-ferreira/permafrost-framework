"""
permafrost.schema — Schema Evolution robusta
=============================================

Define o schema esperado de um arquivo .permafrost e aplica regras de
evolução na leitura: colunas novas recebem default, colunas removidas
são ignoradas, renomeações são declaradas explicitamente, tipos são
promovidos automaticamente.

Uso básico::

    import permafrost as pf

    schema = pf.Schema({
        "cliente_id": pf.Field("int32", required=True),
        "nome":       pf.Field("object", required=True),
        "regiao":     pf.Field("category", required=False, default="Desconhecida"),
        "score":      pf.Field("float64", required=False, default=0.0),
    })

    # Gravar schema junto com o arquivo
    pf.freeze(df, "clientes.permafrost", schema=schema)

    # Ler e aplicar evolução automaticamente
    df = pf.unfreeze("clientes.permafrost", schema=schema)

    # Declarar renomeação explícita
    schema_v2 = pf.Schema({
        "cliente_id": pf.Field("int32", renamed_from="user_id"),
        "nome":       pf.Field("object"),
        "pontuacao":  pf.Field("float64", renamed_from="score"),
    })
    df = pf.unfreeze("clientes_v1.permafrost", schema=schema_v2)
"""

from __future__ import annotations

import json
import os
import numpy as np
import pandas as pd
from typing import Any, Optional


class SchemaError(Exception):
    """Raised when a schema constraint is violated."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_default_series(n: int, dtype: str, default: Any, name: str) -> pd.Series:
    """Create an n-length Series filled with *default* (or null if None)."""
    dtype_low = dtype.lower() if dtype else ""
    if default is None:
        if "int" in dtype_low:
            return pd.Series(pd.array([pd.NA] * n, dtype="Int64"), name=name)
        if "float" in dtype_low:
            return pd.Series(np.full(n, np.nan), dtype="float64", name=name)
        if "datetime" in dtype_low:
            return pd.Series([pd.NaT] * n, dtype="datetime64[ns]", name=name)
        return pd.Series([None] * n, dtype=object, name=name)
    arr = [default] * n
    s = pd.Series(arr, name=name)
    try:
        s = s.astype(dtype)
    except (ValueError, TypeError):
        pass
    return s


_BOOL_MAP = {"True": True, "False": False, "true": True, "false": False, "1": True, "0": False}


def _cast_to_dtype(series: pd.Series, dtype: str, col_name: str) -> pd.Series:
    """Cast *series* to *dtype*. Raises SchemaError on failure."""
    if not dtype:
        return series
    try:
        pd_dtype = pd.api.types.pandas_dtype(dtype)
        if series.dtype == pd_dtype:
            return series

        # Special case: categorical/string → bool (codec stores bool as PRED_CATEGORY
        # with string categories "False"/"True"; plain astype(bool) gives True for any
        # non-empty string, so we need an explicit mapping).
        if dtype in ("bool",) and (
            hasattr(series, "cat")
            or pd.api.types.is_string_dtype(series.dtype)
            or series.dtype == object
        ):
            as_str = series.astype(str)
            mapped = as_str.map(_BOOL_MAP)
            if not mapped.isna().any():
                return mapped.astype(bool)

        return series.astype(pd_dtype)
    except (ValueError, TypeError, OverflowError) as exc:
        raise SchemaError(
            f"Cannot cast column '{col_name}' from '{series.dtype}' to '{dtype}': {exc}"
        ) from exc


# ── Field ─────────────────────────────────────────────────────────────────────

class Field:
    """Declaração de um campo no schema.

    Args:
        dtype: Pandas dtype string — ``"int32"``, ``"float64"``, ``"object"``,
            ``"datetime64[ns]"``, ``"category"``, etc.
        required: Se ``True`` (padrão), levanta :exc:`SchemaError` ao ler um
            arquivo onde a coluna está ausente e não há ``renamed_from``.
        default: Valor padrão para preencher quando a coluna está ausente no
            arquivo. Ignorado se ``required=True``.
        renamed_from: Nome da coluna no arquivo antigo que deve ser mapeado
            para este campo.
    """

    __slots__ = ("dtype", "required", "default", "renamed_from")

    def __init__(
        self,
        dtype: str,
        *,
        required: bool = True,
        default: Any = None,
        renamed_from: Optional[str] = None,
    ):
        self.dtype = dtype
        self.required = required
        self.default = default
        self.renamed_from = renamed_from

        # Validate default compatibility at definition time
        if default is not None:
            try:
                pd.Series([default]).astype(dtype)
            except (ValueError, TypeError) as exc:
                raise SchemaError(
                    f"Default value {default!r} is not compatible with dtype '{dtype}': {exc}"
                ) from exc

    def __repr__(self) -> str:
        parts = [f"dtype={self.dtype!r}"]
        if not self.required:
            parts.append("required=False")
        if self.default is not None:
            parts.append(f"default={self.default!r}")
        if self.renamed_from:
            parts.append(f"renamed_from={self.renamed_from!r}")
        return f"Field({', '.join(parts)})"

    def _to_dict(self) -> dict:
        return {
            "dtype": self.dtype,
            "required": self.required,
            "default": self.default,
            "renamed_from": self.renamed_from,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> "Field":
        return cls(
            data["dtype"],
            required=data.get("required", True),
            default=data.get("default"),
            renamed_from=data.get("renamed_from"),
        )


# ── Schema ────────────────────────────────────────────────────────────────────

class Schema:
    """Schema declarativo para arquivos .permafrost/.pf.

    Args:
        fields: Mapeamento ``{nome_coluna: Field}``.
        version: Versão do schema (inteiro). Gravado no arquivo via
            ``freeze(schema=...)``.

    Raises:
        SchemaError: Se dois campos compartilharem o mesmo ``renamed_from``.
    """

    def __init__(self, fields: dict[str, Field], version: int = 1):
        self.fields: dict[str, Field] = dict(fields)
        self.version: int = version
        self._validate_definition()

    def _validate_definition(self) -> None:
        seen: dict[str, str] = {}
        for name, field in self.fields.items():
            if field.renamed_from:
                if field.renamed_from in seen:
                    raise SchemaError(
                        f"Two fields share renamed_from='{field.renamed_from}': "
                        f"'{seen[field.renamed_from]}' and '{name}'"
                    )
                seen[field.renamed_from] = name

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self, df: pd.DataFrame) -> list[str]:
        """Validates *df* against this schema.

        Returns:
            List of error strings. Empty list means the DataFrame is valid.
        """
        errors: list[str] = []
        for name, field in self.fields.items():
            source = self._find_source(df, name, field)
            if source is None:
                if field.required:
                    msg = f"Required column '{name}' is missing"
                    if field.renamed_from:
                        msg += f" (also checked renamed_from='{field.renamed_from}')"
                    errors.append(msg)
            else:
                try:
                    _cast_to_dtype(df[source], field.dtype, name)
                except SchemaError as exc:
                    errors.append(str(exc))
        return errors

    # ── Application ───────────────────────────────────────────────────────────

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Applies this schema to *df*, returning a new DataFrame.

        Rules:
        - Column present (possibly via renamed_from) → cast to target dtype
        - Column absent, required=True → raises :exc:`SchemaError`
        - Column absent, required=False → filled with default (or null)
        - Columns in df but NOT in schema → dropped silently

        Raises:
            SchemaError: If a required column is missing or a cast fails.
        """
        result: dict[str, pd.Series] = {}
        n = len(df)
        for name, field in self.fields.items():
            source = self._find_source(df, name, field)
            if source is not None:
                series = df[source].copy()
                series.name = name
                result[name] = _cast_to_dtype(series, field.dtype, name)
            else:
                if field.required:
                    msg = f"Required column '{name}' is missing from the file"
                    if field.renamed_from:
                        msg += f" (also checked renamed_from='{field.renamed_from}')"
                    raise SchemaError(msg)
                result[name] = _make_default_series(n, field.dtype, field.default, name)
        return pd.DataFrame(result)

    # ── Inference ─────────────────────────────────────────────────────────────

    @classmethod
    def infer(cls, df: pd.DataFrame, version: int = 1) -> "Schema":
        """Infers a Schema from the dtypes of *df*.

        All columns are marked ``required=True`` with no defaults.
        """
        fields = {col: Field(str(df[col].dtype)) for col in df.columns}
        return cls(fields, version=version)

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str | os.PathLike) -> None:
        """Saves this schema to a JSON file."""
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(self._to_dict(), fp, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Schema":
        """Loads a Schema from a JSON file previously saved with :meth:`save`."""
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        return cls._from_dict(data)

    # ── Serialisation helpers ─────────────────────────────────────────────────

    def _to_dict(self) -> dict:
        return {
            "version": self.version,
            "fields": {name: f._to_dict() for name, f in self.fields.items()},
        }

    @classmethod
    def _from_dict(cls, data: dict) -> "Schema":
        fields = {
            name: Field._from_dict(fdata)
            for name, fdata in data["fields"].items()
        }
        return cls(fields, version=data.get("version", 1))

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _find_source(self, df: pd.DataFrame, name: str, field: Field) -> Optional[str]:
        """Returns the column name in *df* that maps to *name*, or None."""
        if field.renamed_from and field.renamed_from in df.columns:
            return field.renamed_from
        if name in df.columns:
            return name
        return None

    def __repr__(self) -> str:
        lines = [f"Schema(version={self.version}, fields={{"]
        for name, field in self.fields.items():
            lines.append(f"  {name!r}: {field!r},")
        lines.append("})")
        return "\n".join(lines)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Schema):
            return NotImplemented
        return self.version == other.version and self.fields == other.fields

"""
Testes para pf.Schema / pf.Field (Schema Evolution robusta)
============================================================
Executar: pytest tests/test_schema.py -v
"""
import os
import numpy as np
import pandas as pd
import pytest

import permafrost as pf
from permafrost.schema import Schema, Field, SchemaError


# ─────────────────────────── fixtures ────────────────────────────────────────

@pytest.fixture
def df_base():
    return pd.DataFrame({
        "id":     pd.array([1, 2, 3], dtype="int32"),
        "nome":   ["Alice", "Bob", "Carol"],
        "score":  [9.5, 8.0, 7.3],
        "ativo":  [True, False, True],
    })


@pytest.fixture
def schema_base():
    return pf.Schema({
        "id":    pf.Field("int32",   required=True),
        "nome":  pf.Field("object",  required=True),
        "score": pf.Field("float64", required=True),
        "ativo": pf.Field("bool",    required=True),
    })


# ─────────────────────────── Field ───────────────────────────────────────────

def test_field_basic():
    f = pf.Field("int32")
    assert f.dtype == "int32"
    assert f.required is True
    assert f.default is None
    assert f.renamed_from is None


def test_field_optional_with_default():
    f = pf.Field("float64", required=False, default=0.0)
    assert not f.required
    assert f.default == 0.0


def test_field_incompatible_default_raises():
    with pytest.raises(SchemaError, match="not compatible"):
        pf.Field("int32", required=False, default="nao_e_numero")


def test_field_renamed_from():
    f = pf.Field("int64", renamed_from="user_id")
    assert f.renamed_from == "user_id"


# ─────────────────────────── Schema definition ───────────────────────────────

def test_schema_duplicate_renamed_from_raises():
    with pytest.raises(SchemaError, match="share renamed_from"):
        pf.Schema({
            "a": pf.Field("int32", renamed_from="x"),
            "b": pf.Field("int64", renamed_from="x"),
        })


def test_schema_repr():
    s = pf.Schema({"id": pf.Field("int32")})
    assert "Schema" in repr(s)
    assert "id" in repr(s)


# ─────────────────────────── validate ────────────────────────────────────────

def test_schema_validate_valid_df(df_base, schema_base):
    errors = schema_base.validate(df_base)
    assert errors == []


def test_schema_validate_invalid_df_missing_required():
    schema = pf.Schema({
        "id":   pf.Field("int32", required=True),
        "nome": pf.Field("object", required=True),
    })
    df = pd.DataFrame({"id": [1, 2]})  # nome ausente
    errors = schema.validate(df)
    assert any("nome" in e for e in errors)


def test_schema_validate_incompatible_type():
    schema = pf.Schema({
        "id": pf.Field("int32", required=True),
    })
    df = pd.DataFrame({"id": ["nao_e_numero", "outro"]})
    errors = schema.validate(df)
    assert len(errors) > 0
    assert "id" in errors[0]


# ─────────────────────────── apply ───────────────────────────────────────────

def test_schema_new_column_gets_default(df_base):
    schema = pf.Schema({
        "id":     pf.Field("int32"),
        "nome":   pf.Field("object"),
        "score":  pf.Field("float64"),
        "ativo":  pf.Field("bool"),
        "regiao": pf.Field("object", required=False, default="Desconhecida"),
    })
    result = schema.apply(df_base)
    assert "regiao" in result.columns
    assert (result["regiao"] == "Desconhecida").all()


def test_schema_extra_column_dropped(df_base):
    schema = pf.Schema({
        "id":   pf.Field("int32"),
        "nome": pf.Field("object"),
        # score e ativo ausentes do schema → devem ser dropados
    })
    result = schema.apply(df_base)
    assert list(result.columns) == ["id", "nome"]
    assert "score" not in result.columns


def test_schema_rename(tmp_path, df_base):
    path = str(tmp_path / "rename.permafrost")
    pf.freeze(df_base, path)

    schema_v2 = pf.Schema({
        "id":        pf.Field("int32"),
        "nome":      pf.Field("object"),
        "pontuacao": pf.Field("float64", renamed_from="score"),
        "ativo":     pf.Field("bool"),
    })
    df = pf.unfreeze(path, schema=schema_v2)
    assert "pontuacao" in df.columns
    assert "score" not in df.columns
    assert list(df["pontuacao"]) == pytest.approx([9.5, 8.0, 7.3])


def test_schema_type_cast_int32_to_int64(df_base):
    schema = pf.Schema({
        "id":    pf.Field("int64"),   # era int32 no df_base
        "nome":  pf.Field("object"),
        "score": pf.Field("float64"),
        "ativo": pf.Field("bool"),
    })
    result = schema.apply(df_base)
    assert result["id"].dtype == np.int64


def test_schema_incompatible_type_raises(df_base):
    schema = pf.Schema({
        "nome": pf.Field("int32"),   # "Alice" nao converte para int32
    })
    with pytest.raises(SchemaError, match="Cannot cast"):
        schema.apply(df_base)


def test_schema_required_missing_raises():
    schema = pf.Schema({
        "id":   pf.Field("int32", required=True),
        "nome": pf.Field("object", required=True),
    })
    df = pd.DataFrame({"id": [1, 2]})  # nome ausente
    with pytest.raises(SchemaError, match="nome"):
        schema.apply(df)


def test_schema_null_fill_for_optional_no_default():
    schema = pf.Schema({
        "id":    pf.Field("int32"),
        "extra": pf.Field("float64", required=False),  # sem default
    })
    df = pd.DataFrame({"id": pd.array([1, 2, 3], dtype="int32")})
    result = schema.apply(df)
    assert "extra" in result.columns
    assert result["extra"].isna().all()


# ─────────────────────────── freeze / unfreeze round-trip ────────────────────

def test_schema_stored_in_audit(tmp_path, df_base, schema_base):
    path = str(tmp_path / "audit_schema.permafrost")
    pf.freeze(df_base, path, schema=schema_base)

    info = pf.audit(path)
    assert info["schema_version"] == 1
    assert info["schema"] is not None
    assert "id" in info["schema"]
    assert info["schema"]["id"]["dtype"] == "int32"


def test_schema_freeze_unfreeze_roundtrip(tmp_path, df_base, schema_base):
    path = str(tmp_path / "schema_rt.permafrost")
    pf.freeze(df_base, path, schema=schema_base)
    df_back = pf.unfreeze(path, schema=schema_base)
    # check_dtype=False: pandas 2.x infers string cols como StringDtype vs object
    pd.testing.assert_frame_equal(df_base, df_back, check_like=False, check_dtype=False)


def test_schema_unfreeze_without_freeze_schema(tmp_path, df_base):
    """Schema aplicado so no unfreeze (arquivo sem schema gravado) ainda funciona."""
    path = str(tmp_path / "no_schema.permafrost")
    pf.freeze(df_base, path)

    schema = pf.Schema({
        "id":    pf.Field("int64"),     # promocao int32->int64
        "nome":  pf.Field("object"),
        "score": pf.Field("float64"),
        "ativo": pf.Field("bool"),
    })
    df = pf.unfreeze(path, schema=schema)
    assert df["id"].dtype == np.int64


def test_audit_no_schema_returns_none(tmp_path, df_base):
    """Arquivo sem schema gravado: schema_version e schema sao None."""
    path = str(tmp_path / "no_schema.permafrost")
    pf.freeze(df_base, path)
    info = pf.audit(path)
    assert info["schema_version"] is None
    assert info["schema"] is None


# ─────────────────────────── infer ───────────────────────────────────────────

def test_schema_infer_from_dataframe(df_base):
    schema = pf.Schema.infer(df_base)
    assert set(schema.fields.keys()) == set(df_base.columns)
    for col in df_base.columns:
        assert schema.fields[col].dtype == str(df_base[col].dtype)
    assert schema.version == 1


# ─────────────────────────── save / load ─────────────────────────────────────

def test_schema_save_load_roundtrip(tmp_path, schema_base):
    path = str(tmp_path / "schema.json")
    schema_base.save(path)
    loaded = pf.Schema.load(path)

    assert loaded.version == schema_base.version
    assert set(loaded.fields.keys()) == set(schema_base.fields.keys())
    for name in schema_base.fields:
        orig = schema_base.fields[name]
        restored = loaded.fields[name]
        assert restored.dtype == orig.dtype
        assert restored.required == orig.required
        assert restored.default == orig.default
        assert restored.renamed_from == orig.renamed_from


def test_schema_save_load_with_renamed_from(tmp_path):
    schema = pf.Schema({
        "cliente_id": pf.Field("int32", renamed_from="user_id"),
        "pontuacao":  pf.Field("float64", required=False, default=0.0,
                               renamed_from="score"),
    })
    path = str(tmp_path / "renamed.json")
    schema.save(path)
    loaded = pf.Schema.load(path)
    assert loaded.fields["cliente_id"].renamed_from == "user_id"
    assert loaded.fields["pontuacao"].renamed_from == "score"
    assert loaded.fields["pontuacao"].default == 0.0

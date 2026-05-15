"""
PermafrostSchemaDetector — detecta tipo de dado e faz flatten automático
para dados semi-estruturados (JSONL, MongoDB, DynamoDB, etc.)

Suporta:
  - DataFrame tabular puro → passa direto
  - list[dict] / JSONL → flatten + encoding híbrido
  - Arquivo .csv, .jsonl, .json, .parquet → leitura automática
"""
import json, io, os
from enum import Enum
from typing import Union
import pandas as pd
import numpy as np

class DataType(str, Enum):
    TABULAR        = "tabular"         # DataFrame puro / CSV
    SEMI_STRUCT    = "semi_structured" # JSONL / MongoDB / dicts com arrays/nested
    TEXT_STREAM    = "text_stream"     # logs, emails, HTML
    BINARY         = "binary"          # imagens, vídeos — não compressível

class FieldKind(str, Enum):
    SCALAR   = "scalar"    # int, float, bool, str, datetime
    ARRAY    = "array"     # lista de escalares ou objetos
    NESTED   = "nested"    # dict aninhado
    NULLABLE = "nullable"  # campo que pode ser None

class SchemaDetector:
    """
    Analisa uma amostra de dados e decide a melhor estratégia de encoding.
    """

    MIN_PRESENCE = 0.60   # campo deve aparecer em ≥60% dos docs para virar coluna
    MAX_ARRAY_TOKENS = 512  # dicionário máximo para json_arr_dict

    def __init__(self, sample_size: int = 500):
        self.sample_size = sample_size

    def detect_file(self, path: str) -> tuple:
        """
        Lê um arquivo e retorna (DataFrame, DataType, field_manifest).
        Suporta: .csv, .jsonl, .json, .parquet, .xlsx
        """
        ext = os.path.splitext(path)[1].lower()

        if ext == '.csv':
            df = pd.read_csv(path)
            return df, DataType.TABULAR, self._tabular_manifest(df)

        elif ext in ('.jsonl', '.ndjson'):
            with open(path, 'r', encoding='utf-8') as f:
                docs = [json.loads(line) for line in f if line.strip()]
            return self.flatten(docs)

        elif ext == '.json':
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                return self.flatten(data)
            elif isinstance(data, dict):
                # JSON único — tratar como 1 documento
                return self.flatten([data])
            else:
                raise ValueError(f"JSON não reconhecido: {type(data)}")

        elif ext == '.parquet':
            df = pd.read_parquet(path)
            return df, DataType.TABULAR, self._tabular_manifest(df)

        elif ext in ('.xlsx', '.xls'):
            df = pd.read_excel(path)
            return df, DataType.TABULAR, self._tabular_manifest(df)

        else:
            raise ValueError(f"Formato não suportado: {ext}. Suportados: .csv .jsonl .json .parquet .xlsx")

    def detect(self, data) -> tuple:
        """
        Detecta o tipo de 'data' e retorna (DataFrame, DataType, field_manifest).
        data pode ser: pd.DataFrame, list[dict], str (caminho de arquivo)
        """
        if isinstance(data, str):
            return self.detect_file(data)
        elif isinstance(data, pd.DataFrame):
            # Verificar se tem colunas com listas/dicts
            has_complex = any(
                data[col].dropna().apply(lambda x: isinstance(x, (list, dict))).any()
                for col in data.columns
            )
            if has_complex:
                docs = data.to_dict('records')
                return self.flatten(docs)
            return data, DataType.TABULAR, self._tabular_manifest(data)
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            return self.flatten(data)
        else:
            raise ValueError(f"Tipo não suportado: {type(data)}")

    def flatten(self, docs: list) -> tuple:
        """
        Converte list[dict] em DataFrame flat + manifesto de campos.
        - Campos escalares → colunas diretas
        - Arrays de strings curtas → coluna com json_arr encoding
        - Arrays de objetos / nested dicts → coluna JSON serializada
        - Campos ausentes/None → preenchidos com valor padrão
        """
        if not docs:
            return pd.DataFrame(), DataType.SEMI_STRUCT, {}

        sample = docs[:self.sample_size]
        n = len(sample)

        # 1. Coletar todos os campos e sua frequência
        field_counts = {}
        field_types  = {}   # campo → set de tipos Python
        for doc in sample:
            for k, v in doc.items():
                field_counts[k] = field_counts.get(k, 0) + 1
                field_types.setdefault(k, set()).add(type(v).__name__)

        # 2. Classificar cada campo
        field_manifest = {}
        scalar_cols = []
        complex_cols = []   # arrays ou nested

        for field, count in field_counts.items():
            presence = count / n
            types    = field_types[field]
            nullable = 'NoneType' in types
            real_types = types - {'NoneType'}

            # Pegar uma amostra de valores não-nulos
            sample_vals = [doc.get(field) for doc in sample
                           if doc.get(field) is not None][:20]

            kind = self._classify_field(field, real_types, sample_vals)

            manifest_entry = {
                'presence': round(presence, 3),
                'nullable': nullable,
                'kind':     kind.value,
                'py_types': list(real_types),
            }

            if presence >= self.MIN_PRESENCE:
                if kind == FieldKind.SCALAR:
                    scalar_cols.append(field)
                    manifest_entry['strategy'] = 'column_scalar'
                elif kind == FieldKind.ARRAY:
                    # Sub-classificar: array de strings curtas → dict encoding
                    if self._is_short_string_array(sample_vals):
                        manifest_entry['strategy'] = 'json_arr_dict'
                        manifest_entry['token_dict'] = self._build_token_dict(docs, field)
                    else:
                        manifest_entry['strategy'] = 'json_str'
                    complex_cols.append(field)
                elif kind == FieldKind.NESTED:
                    manifest_entry['strategy'] = 'json_str'
                    complex_cols.append(field)
            else:
                manifest_entry['strategy'] = 'json_str_rare'  # campo raro → JSON

            field_manifest[field] = manifest_entry

        # 3. Construir DataFrame
        rows = []
        all_scalar_fields = scalar_cols
        all_complex_fields = complex_cols

        for doc in docs:
            row = {}
            # Campos escalares diretos
            for f in all_scalar_fields:
                row[f] = doc.get(f)
            # Campos complexos → serialize to JSON string
            for f in all_complex_fields:
                v = doc.get(f)
                row[f] = json.dumps(v, ensure_ascii=False) if v is not None else 'null'
            # Campos raros → concatenar num campo _meta
            rows.append(row)

        df = pd.DataFrame(rows)

        # 4. Converter tipos
        for col in all_scalar_fields:
            if col not in df.columns:
                continue
            m = field_manifest.get(col, {})
            py_types = set(m.get('py_types', []))

            # Tentar converter timestamps
            if 'str' in py_types and df[col].notna().any():
                sample_val = df[col].dropna().iloc[0] if len(df[col].dropna()) > 0 else None
                if sample_val and isinstance(sample_val, str):
                    if 'T' in sample_val or '-' in sample_val[:10]:
                        try:
                            df[col] = pd.to_datetime(df[col], errors='coerce', utc=True).dt.tz_localize(None)
                            field_manifest[col]['py_types'] = ['datetime']
                            continue
                        except:
                            pass

            if 'int' in py_types and 'float' not in py_types:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')
            elif 'float' in py_types or ('int' in py_types and 'float' in py_types):
                df[col] = pd.to_numeric(df[col], errors='coerce')
            elif 'bool' in py_types:
                df[col] = df[col].astype('boolean')

        return df, DataType.SEMI_STRUCT, field_manifest

    def _classify_field(self, name, types, sample_vals) -> FieldKind:
        scalar_types = {'int','float','str','bool','NoneType'}
        if types <= scalar_types:
            return FieldKind.SCALAR
        if 'list' in types:
            return FieldKind.ARRAY
        if 'dict' in types:
            return FieldKind.NESTED
        return FieldKind.SCALAR

    def _is_short_string_array(self, sample_vals) -> bool:
        """Array de strings curtas → candidato a token dict encoding."""
        for v in sample_vals:
            if not isinstance(v, list): return False
            if len(v) > 20: return False  # array muito grande
            if any(not isinstance(s, str) or len(s) > 50 for s in v):
                return False
        return True

    def _build_token_dict(self, docs, field, max_tokens=256) -> list:
        """Constrói dicionário de tokens mais frequentes para um campo de array."""
        from collections import Counter
        counter = Counter()
        for doc in docs:
            v = doc.get(field)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        counter[item] += 1
        return [tok for tok, _ in counter.most_common(max_tokens)]

    def _tabular_manifest(self, df) -> dict:
        return {col: {'kind': FieldKind.SCALAR.value, 'strategy': 'column_scalar',
                      'presence': 1.0, 'nullable': df[col].isna().any(),
                      'py_types': [str(df[col].dtype)]}
                for col in df.columns}

print("permafrost_schema_detector.py OK")
print("  Classes: SchemaDetector, DataType, FieldKind")

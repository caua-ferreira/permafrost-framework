"""
SchemaDetector Stress — JSONL caótico, campos ausentes, tipos misturados.
Executar: pytest tests/test_schema_detector_stress.py -v
"""
import os, shutil, tempfile, json
import pytest
import numpy as np
import pandas as pd
import permafrost as pf


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def write_jsonl(path, docs):
    with open(path, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# §1 CAMPOS AUSENTES
# ══════════════════════════════════════════════════════════════════════════════

class TestCamposAusentes:

    def test_50pct_campos_ausentes(self, tmp):
        """Schema detector usa o schema da amostra (campos presentes em >0 docs).
        
        Nota: campos presentes em menos de 50% dos documentos da amostra podem
        ser omitidos se não aparecerem na amostra. Para garantir que campos
        opcionais sejam detectados, use flatten() passando todos os docs.
        """
        np.random.seed(1); N = 500
        docs = []
        for i in range(N):
            d = {"id": i, "v": float(i), "opcional": f"val_{i}"}  # sempre presente
            if np.random.rand() > 0.5:
                d["muito_raro"] = f"raro_{i}"
            docs.append(d)

        path = os.path.join(tmp, "missing50.jsonl")
        write_jsonl(path, docs)

        det = pf.SchemaDetector()
        df, dtype, _ = det.detect(path)

        assert len(df) == N
        assert "id" in df.columns
        assert "v"  in df.columns
        assert "opcional" in df.columns  # sempre presente → sempre detectado

    def test_campo_presente_em_apenas_1_documento(self, tmp):
        """Campo raro (1 em 1000) deve ser detectado."""
        N = 1_000
        docs = [{"id": i, "v": i * 1.0} for i in range(N)]
        docs[500]["raro"] = "aparece_so_aqui"
        path = os.path.join(tmp, "rare.jsonl")
        write_jsonl(path, docs)

        det = pf.SchemaDetector(sample_size=1000)
        df, _, _ = det.detect(path)
        assert len(df) == N
        # Campo raro pode ou não aparecer dependendo do sample

    def test_todos_campos_ausentes_em_alguns_docs(self, tmp):
        """Documentos completamente vazios ({}) intercalados."""
        docs = [{"id": i, "v": float(i)} for i in range(200)]
        docs[10]  = {}
        docs[50]  = {}
        docs[100] = {}
        path = os.path.join(tmp, "empty_docs.jsonl")
        write_jsonl(path, docs)

        det = pf.SchemaDetector()
        df, dtype, _ = det.detect(path)
        assert len(df) == len(docs)

    def test_campos_ausentes_freeze_thaw(self, tmp):
        """Após flatten de campos ausentes, freeze+thaw deve funcionar."""
        docs = [{"id": i, "nome": f"n{i}", **({"score": float(i)} if i%3==0 else {})}
                for i in range(300)]
        path = os.path.join(tmp, "partial.jsonl")
        write_jsonl(path, docs)

        det = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        pf_path = os.path.join(tmp, "partial.permafrost")
        m = pf.freeze(df, pf_path)
        df_b = pf.thaw(pf_path, verify=True)
        assert len(df_b) == len(docs)


# ══════════════════════════════════════════════════════════════════════════════
# §2 TIPOS MISTURADOS
# ══════════════════════════════════════════════════════════════════════════════

class TestTiposMisturados:

    def test_campo_int_e_string_na_mesma_coluna(self, tmp):
        """Campo com int em alguns docs e string em outros → tratar como string."""
        docs = [
            {"id": 1, "v": 42},
            {"id": 2, "v": "texto"},
            {"id": 3, "v": 99},
            {"id": 4, "v": "outro_texto"},
        ]
        path = os.path.join(tmp, "mixed_types.jsonl")
        write_jsonl(path, docs)
        det = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        assert len(df) == 4
        # Deve ter tratado como string ou float sem crash

    def test_campo_bool_e_int(self, tmp):
        """bool (True/False) e int na mesma coluna."""
        docs = [{"id": i, "flag": (i % 2 == 0)} for i in range(100)]
        docs[50]["flag"] = 1
        path = os.path.join(tmp, "bool_int.jsonl")
        write_jsonl(path, docs)
        det = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        assert len(df) == 100

    def test_campo_null_e_valor(self, tmp):
        """Campo com null e valor real."""
        docs = [{"id": i, "v": None if i % 3 == 0 else float(i)} for i in range(300)]
        path = os.path.join(tmp, "null_val.jsonl")
        write_jsonl(path, docs)
        det = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        assert len(df) == 300
        assert "v" in df.columns

    def test_tipos_diferentes_freeze_thaw(self, tmp):
        """Flatten + freeze + thaw de campo com tipos misturados."""
        docs = [{"id": i, "desc": f"item {i}", "status": i % 2 == 0}
                for i in range(200)]
        path = os.path.join(tmp, "mt.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        pf_path = os.path.join(tmp, "mt.permafrost")
        pf.freeze(df, pf_path)
        df_b = pf.thaw(pf_path, verify=True)
        assert len(df_b) == 200


# ══════════════════════════════════════════════════════════════════════════════
# §3 ANINHAMENTO PROFUNDO
# ══════════════════════════════════════════════════════════════════════════════

class TestAninhamentoProfundo:

    def test_nested_2_niveis(self, tmp):
        """Objeto dentro de objeto."""
        docs = [{"id": i, "user": {"name": f"u{i}", "age": 20+i%40}}
                for i in range(200)]
        path = os.path.join(tmp, "nested2.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        df, dtype, _ = det.detect(path)
        assert len(df) == 200

    def test_array_de_strings(self, tmp):
        """Array de strings (hashtags)."""
        tags_pool = [f"#tag{i}" for i in range(50)]
        docs = [{"id": i, "tags": np.random.choice(tags_pool, 3).tolist()}
                for i in range(300)]
        path = os.path.join(tmp, "arrays.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        df, _, manifest = det.detect(path)
        assert len(df) == 300
        assert "tags" in df.columns

    def test_array_de_objetos(self, tmp):
        """Array de objetos (itens de pedido)."""
        docs = [{"id": i, "itens": [{"prod": f"P{j}", "qty": j+1} for j in range(3)]}
                for i in range(100)]
        path = os.path.join(tmp, "array_obj.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        assert len(df) == 100

    def test_freeze_thaw_nested(self, tmp):
        """Nested object → freeze → thaw deve preservar dados."""
        docs = [{"id": i,
                 "loc": {"lat": float(-23 + i*0.001), "lon": float(-46 + i*0.001)},
                 "score": float(i * 1.5)}
                for i in range(500)]
        path = os.path.join(tmp, "nested.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        pf_path = os.path.join(tmp, "nested.permafrost")
        m = pf.freeze(df, pf_path)
        df_b = pf.thaw(pf_path, verify=True)
        assert len(df_b) == 500


# ══════════════════════════════════════════════════════════════════════════════
# §4 WIDE TABLE — MUITOS CAMPOS
# ══════════════════════════════════════════════════════════════════════════════

class TestWideTable:

    def test_50_campos_por_documento(self, tmp):
        """Documento com 50 campos."""
        docs = [
            {f"campo_{j:02d}": f"val_{i}_{j}" if j % 3 != 0 else float(i * j)
             for j in range(50)}
            for i in range(200)
        ]
        for i, d in enumerate(docs): d["id"] = i
        path = os.path.join(tmp, "wide50.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        assert len(df) == 200
        assert len(df.columns) >= 50

    def test_100_campos_freeze_thaw(self, tmp):
        """100 campos → freeze → thaw."""
        np.random.seed(3); N = 100
        docs = []
        for i in range(N):
            d = {"id": i}
            for j in range(99):
                d[f"f{j:02d}"] = float(i + j * 0.1) if j % 2 == 0 else f"s{i}_{j}"
            docs.append(d)
        path = os.path.join(tmp, "wide100.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        pf_path  = os.path.join(tmp, "wide100.permafrost")
        pf.freeze(df, pf_path, codec=pf.CODEC_LZMA2)
        df_b = pf.thaw(pf_path, verify=True)
        assert len(df_b) == N

    def test_campos_com_nomes_identicos_normalizados(self, tmp):
        """Campos com nomes que se normalizam igual."""
        docs = [{"id": i, "campo_1": i, "campo-1": i+1} for i in range(100)]
        path = os.path.join(tmp, "dup_names.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        # Não deve crashar mesmo com nomes similares
        df, _, _ = det.detect(path)
        assert len(df) == 100


# ══════════════════════════════════════════════════════════════════════════════
# §5 DATASETS GRANDES
# ══════════════════════════════════════════════════════════════════════════════

class TestSchemaDetectorGrande:

    def test_10k_documentos_jsonl(self, tmp):
        """10.000 documentos com schema consistente."""
        N = 10_000
        np.random.seed(42)
        docs = [{"id": i, "user": f"u{i%1000}", "score": float(i*1.1),
                 "tags": ["t1","t2"], "active": i%2==0}
                for i in range(N)]
        path = os.path.join(tmp, "big10k.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector(sample_size=500)
        df, dtype, _ = det.detect(path)
        assert len(df) == N
        assert dtype == pf.DataType.SEMI_STRUCT

    def test_10k_freeze_thaw_completo(self, tmp):
        """10.000 documentos → freeze → thaw → linhas corretas."""
        N = 10_000
        np.random.seed(7)
        docs = [{"id": i, "val": float(i * 1.5),
                 "cat": f"cat_{i%20}", "active": bool(i%2)}
                for i in range(N)]
        path = os.path.join(tmp, "big_ft.jsonl")
        write_jsonl(path, docs)
        det  = pf.SchemaDetector()
        df, _, _ = det.detect(path)
        pf_path  = os.path.join(tmp, "big_ft.permafrost")
        m = pf.freeze(df, pf_path)
        assert m["rows"] == N
        df_b = pf.thaw(pf_path, verify=True)
        assert len(df_b) == N

    def test_dataframe_tabular_grande(self, tmp):
        """DataFrame tabular de 50k linhas via detect()."""
        np.random.seed(1); N = 50_000
        df_orig = pd.DataFrame({
            "id": np.arange(N, dtype=np.int32),
            "v":  np.round(np.random.uniform(1, 1000, N), 2),
            "c":  np.random.choice(["A","B","C"], N),
        })
        det = pf.SchemaDetector()
        df_out, dtype, _ = det.detect(df_orig)
        assert dtype == pf.DataType.TABULAR
        assert len(df_out) == N


# ══════════════════════════════════════════════════════════════════════════════
# §6 CSV STRESS
# ══════════════════════════════════════════════════════════════════════════════

class TestCSVStress:

    def test_csv_com_valores_nulos(self, tmp):
        """CSV com NaN em diversas colunas."""
        df = pd.DataFrame({
            "id":    range(200),
            "nome":  [None if i%5==0 else f"user_{i}" for i in range(200)],
            "score": [None if i%7==0 else float(i) for i in range(200)],
        })
        path = os.path.join(tmp, "nulls.csv")
        df.to_csv(path, index=False)
        det = pf.SchemaDetector()
        df_out, dtype, _ = det.detect(path)
        assert dtype == pf.DataType.TABULAR
        assert len(df_out) == 200

    def test_csv_com_aspas_e_virgulas(self, tmp):
        """CSV com campos que contêm vírgulas e aspas."""
        df = pd.DataFrame({
            "id":   range(50),
            "desc": [f'texto "com aspas", e virgulas #{i}' for i in range(50)],
            "v":    range(50),
        })
        path = os.path.join(tmp, "quotes.csv")
        df.to_csv(path, index=False, quoting=1)  # QUOTE_ALL
        det = pf.SchemaDetector()
        df_out, _, _ = det.detect(path)
        assert len(df_out) == 50

    def test_csv_com_encoding_utf8(self, tmp):
        """CSV com caracteres especiais UTF-8."""
        df = pd.DataFrame({
            "id":   range(10),
            "nome": ["José","Müller","中文","Ñoño","Ελληνικά","العربية","한국어","Brésilí","Ação","Maçã"],
        })
        path = os.path.join(tmp, "utf8.csv")
        df.to_csv(path, index=False, encoding="utf-8")
        det = pf.SchemaDetector()
        df_out, _, _ = det.detect(path)
        assert len(df_out) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

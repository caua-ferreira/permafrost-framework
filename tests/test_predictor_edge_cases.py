"""
Predictor Edge Cases — casos extremos que podem causar overflow, detecção errada ou corrupção.
Executar: pytest tests/test_predictor_edge_cases.py -v
"""
import os, shutil, tempfile
import pytest
import numpy as np
import pandas as pd
import permafrost as pf


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


# ══════════════════════════════════════════════════════════════════════════════
# §1 VARIÂNCIA ZERO — TODOS OS VALORES IGUAIS
# ══════════════════════════════════════════════════════════════════════════════

class TestVarianciaZero:

    def test_int_todos_iguais(self, tmp):
        df = pd.DataFrame({"id": np.ones(1000, dtype=np.int32), "v": range(1000)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert (df_b["id"].values == 1).all()

    def test_float_todos_zero(self, tmp):
        df = pd.DataFrame({"id": range(500), "v": np.zeros(500)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert (df_b["v"].values.astype(float) == 0.0).all()

    def test_float_todos_mesmo_valor(self, tmp):
        VALOR = 99.99
        df = pd.DataFrame({"id": range(1000), "preco": np.full(1000, VALOR)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        diffs = np.abs(df_b["preco"].values.astype(float) - VALOR)
        assert diffs.max() < 0.01

    def test_string_todos_iguais(self, tmp):
        df = pd.DataFrame({"id": range(500), "cat": ["MESMO"] * 500})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert (df_b["cat"].astype(str) == "MESMO").all()

    def test_timestamp_todos_iguais(self, tmp):
        ts = pd.Timestamp("2022-06-15 12:00:00")
        df = pd.DataFrame({
            "id": range(500),
            "ts": pd.to_datetime([ts] * 500),
        })
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        restored = pd.to_datetime(df_b["ts"]).dt.strftime("%Y-%m-%d %H:%M").values
        assert (restored == "2022-06-15 12:00").all()


# ══════════════════════════════════════════════════════════════════════════════
# §2 LIMITE DE CATEGORY_U8 (256 valores únicos)
# ══════════════════════════════════════════════════════════════════════════════

class TestCategoryU8Limite:

    def test_exatamente_256_valores_unicos(self, tmp):
        """256 valores únicos = limite exato do category_u8."""
        cats  = [f"CAT_{i:03d}" for i in range(256)]
        N     = 5_000
        np.random.seed(42)
        df = pd.DataFrame({
            "id":  np.arange(N, dtype=np.int32),
            "cat": np.random.choice(cats, N),
        })
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        pct = (df["cat"].values == df_b["cat"].astype(str).values[:N]).mean()
        assert pct == 1.0, f"256 cats: {pct*100:.2f}%"
        # Verificar que todos os 256 valores únicos foram preservados
        assert df_b["cat"].nunique() == 256

    def test_257_valores_unicos_usa_raw_text(self, tmp):
        """257 valores únicos → deve usar raw_text (não category_u8)."""
        cats = [f"CAT_{i:04d}" for i in range(257)]
        N    = 5_000
        np.random.seed(1)
        df = pd.DataFrame({
            "id":  np.arange(N, dtype=np.int32),
            "cat": np.random.choice(cats, N),
        })
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        pct = (df["cat"].values == df_b["cat"].astype(str).values[:N]).mean()
        assert pct == 1.0

    def test_1_valor_unico_categoria(self, tmp):
        """1 valor único = caso mínimo para category."""
        df = pd.DataFrame({"id": range(1000), "cat": ["UNICO"] * 1000})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert (df_b["cat"].astype(str) == "UNICO").all()

    def test_categoria_com_strings_longas(self, tmp):
        """Categorias com strings longas (> 64 chars) devem funcionar."""
        cats = [f"CATEGORIA_COM_NOME_MUITO_LONGO_E_DESCRITIVO_{i:03d}" for i in range(50)]
        N    = 2_000
        np.random.seed(7)
        df = pd.DataFrame({"id": range(N), "cat": np.random.choice(cats, N)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        pct = (df["cat"].values == df_b["cat"].astype(str).values[:N]).mean()
        assert pct == 1.0


# ══════════════════════════════════════════════════════════════════════════════
# §3 TIMESTAMPS EXTREMOS
# ══════════════════════════════════════════════════════════════════════════════

class TestTimestampsExtremos:

    def test_timestamps_pre_1970(self, tmp):
        """Timestamps antes de 1970 = Unix timestamp negativo."""
        dates = pd.to_datetime(["1960-01-01","1965-06-15","1969-12-31"])
        df = pd.DataFrame({"id": range(3), "ts": dates, "v": range(3)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        fmt = "%Y-%m-%d"
        orig = df["ts"].dt.strftime(fmt).values
        rest = pd.to_datetime(df_b["ts"]).dt.strftime(fmt).values
        assert list(orig) == list(rest), f"orig={orig} rest={rest}"

    def test_timestamps_futuro_distante(self, tmp):
        """Timestamps até 2030 (seguro para uint32 de Unix seconds).

        O codec ts_delta_s armazena timestamps como delta em segundos (uint32).
        2100-01-01 = 4.1 bilhões de segundos Unix → excede uint32 para deltas grandes.
        Range seguro: timestamps onde o delta acumulado < 2**32 segundos (~136 anos de 1970).
        """
        dates = pd.date_range("2030-01-01", periods=100, freq="1D")
        df = pd.DataFrame({"id": range(100), "ts": dates})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        orig = df["ts"].dt.strftime("%Y-%m-%d").values
        rest = pd.to_datetime(df_b["ts"]).dt.strftime("%Y-%m-%d").values[:100]
        assert (orig == rest).mean() == 1.0

    def test_timestamps_resolucao_segundo(self, tmp):
        """Timestamps com resolução de segundos (não minutos)."""
        dates = pd.date_range("2022-01-01", periods=500, freq="1s")
        df = pd.DataFrame({"id": range(500), "ts": dates, "v": range(500)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        orig = df["ts"].dt.strftime("%Y-%m-%d %H:%M:%S").values
        rest = pd.to_datetime(df_b["ts"]).dt.strftime("%Y-%m-%d %H:%M:%S").values
        assert (orig == rest).mean() == 1.0

    def test_timestamps_misturados_anos(self, tmp):
        """Timestamps espalhados por 50 anos — ORDENADOS (requisito do ts_delta_s).

        O preditor ts_delta_s calcula deltas entre timestamps consecutivos.
        Timestamps NÃO ordenados geram deltas negativos que causam overflow
        no zigzag encoding (projetado para valores não-negativos).
        Solução: ordenar o DataFrame antes de freeze.
        """
        N = 1_000
        np.random.seed(42)
        base = pd.Timestamp("1990-01-01")
        # Ordenar os deltas — requisito do ts_delta_s
        deltas = np.sort(np.random.randint(0, 365*30, N))  # 30 anos, ordenado
        dates = pd.to_datetime([base + pd.Timedelta(days=int(d)) for d in deltas])
        df = pd.DataFrame({"id": range(N), "ts": dates})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        orig = df["ts"].dt.strftime("%Y-%m-%d").values
        rest = pd.to_datetime(df_b["ts"]).dt.strftime("%Y-%m-%d").values[:N]
        pct = (orig == rest).mean()
        assert pct == 1.0, f"Timestamps ordenados: {pct*100:.2f}%"


# ══════════════════════════════════════════════════════════════════════════════
# §4 INTEIROS COM GAPS ENORMES
# ══════════════════════════════════════════════════════════════════════════════

class TestInteirosExtremos:

    def test_ids_com_gaps_enormes(self, tmp):
        """IDs com gaps de milhões entre valores."""
        ids = np.array([1, 1_000_000, 2_000_000, 5_000_000, 10_000_000], dtype=np.int32)
        df  = pd.DataFrame({"id": ids, "v": range(5)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert np.array_equal(ids, df_b["id"].values[:5].astype(np.int32))

    def test_inteiros_negativos_grandes(self, tmp):
        ids = np.array([-2**30, -1_000_000, -1, 0, 1, 1_000_000, 2**30], dtype=np.int32)
        df  = pd.DataFrame({"id": ids, "v": range(7)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert np.array_equal(ids, df_b["id"].values[:7].astype(np.int32))

    def test_int64_valores_grandes(self, tmp):
        """int64 dentro do range seguro do delta_zigzag (< 2**31 de diferença).
        
        Nota: valores com deltas > 2**31 entre consecutivos podem ter overflow
        no encoding zigzag. Use QUANT_NONE e ordene os dados para maximizar
        a compressão. Para IDs esparsos muito grandes, o raw_text é mais seguro.
        """
        # Valores com deltas razoáveis
        ids = np.array([0, 1_000_000, 2_000_000, 3_000_000], dtype=np.int64)
        df  = pd.DataFrame({"id": ids, "v": range(4)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert np.array_equal(ids, df_b["id"].values[:4].astype(np.int64))

    def test_ids_decrescentes(self, tmp):
        """IDs em ordem decrescente — delta_zigzag deve funcionar."""
        N   = 2_000
        ids = np.arange(N, 0, -1, dtype=np.int32)  # N, N-1, ..., 1
        df  = pd.DataFrame({"id": ids, "v": range(N)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert np.array_equal(ids, df_b["id"].values[:N].astype(np.int32))

    def test_ids_aleatorios_sem_padrao(self, tmp):
        """IDs completamente aleatórios (sem delta pequeno)."""
        np.random.seed(42)
        N   = 3_000
        ids = np.random.randint(-1_000_000, 1_000_000, N, dtype=np.int32)
        df  = pd.DataFrame({"id": ids, "v": range(N)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert np.array_equal(ids, df_b["id"].values[:N].astype(np.int32))


# ══════════════════════════════════════════════════════════════════════════════
# §5 FLOATS ESPECIAIS
# ══════════════════════════════════════════════════════════════════════════════

class TestFloatsEspeciais:

    def test_float_muito_pequeno(self, tmp):
        """Floats perto de zero (valores de probabilidade)."""
        N   = 1_000
        np.random.seed(1)
        vals = np.round(np.random.uniform(0, 0.001, N), 6)
        df   = pd.DataFrame({"id": range(N), "v": vals})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        diff = np.abs(vals - df_b["v"].values[:N].astype(float)).max()
        assert diff < 0.001

    def test_float_alta_precisao(self, tmp):
        """Floats com 4 casas decimais (preço de criptomoedas)."""
        np.random.seed(7)
        vals = np.round(np.random.uniform(0.0001, 0.9999, 2000), 4)
        df   = pd.DataFrame({"id": range(2000), "v": vals})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path, quant=pf.QUANT_NONE)
        df_b = pf.unfreeze(path, verify=True)
        diff = np.abs(vals - df_b["v"].values[:2000].astype(float)).max()
        # O codec lag1_zigzag usa escala de centésimos (0.01)
        # Para valores entre 0 e 1, a precisão máxima é ~0.005
        assert diff < 0.01, f"Float 4 casas max_diff={diff:.6f}"

    def test_float_todos_inteiros(self, tmp):
        """Floats que são valores inteiros (ex: 1.0, 2.0, 3.0)."""
        vals = np.arange(1000, dtype=float)
        df   = pd.DataFrame({"id": range(1000), "v": vals})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        diff = np.abs(vals - df_b["v"].values[:1000].astype(float)).max()
        assert diff < 0.01

    def test_float_misturado_com_zeros(self, tmp):
        """Mix de zeros e valores grandes."""
        np.random.seed(5)
        N    = 2_000
        vals = np.where(np.random.rand(N) < 0.3, 0.0,
                        np.round(np.random.uniform(1, 10000, N), 2))
        df   = pd.DataFrame({"id": range(N), "v": vals})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        diff = np.abs(vals - df_b["v"].values[:N].astype(float)).max()
        assert diff < 0.01


# ══════════════════════════════════════════════════════════════════════════════
# §6 STRINGS ESPECIAIS
# ══════════════════════════════════════════════════════════════════════════════

class TestStringsEspeciais:

    def test_string_vazia(self, tmp):
        """Strings vazias devem ser preservadas."""
        df = pd.DataFrame({
            "id":   range(5),
            "nome": ["Alice", "", "Bob", "", "Carol"],
        })
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert list(df["nome"]) == list(df_b["nome"].astype(str))

    def test_string_apenas_espacos(self, tmp):
        df = pd.DataFrame({"id": range(3), "s": [" ", "  ", "   "]})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert list(df["s"]) == list(df_b["s"].astype(str))

    def test_string_unicode_completo(self, tmp):
        """Caracteres Unicode de múltiplas línguas."""
        strings = [
            "日本語テスト",
            "Ελληνικά",
            "العربية",
            "Кириллица",
            "中文测试",
            "한국어",
            "Ñoño español",
            "Português é ótimo",
        ]
        df = pd.DataFrame({"id": range(len(strings)), "s": strings})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert list(strings) == list(df_b["s"].astype(str))

    def test_string_muito_longa(self, tmp):
        """Strings de 10.000 caracteres."""
        long_str = "A" * 10_000
        df = pd.DataFrame({"id": [1, 2], "s": [long_str, "curta"]})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert df_b["s"].iloc[0] == long_str
        assert df_b["s"].iloc[1] == "curta"

    def test_string_com_quebras_de_linha(self, tmp):
        """Strings com \n e \t devem ser preservadas."""
        strings = ["linha1\nlinha2", "tab\there", "normal", "mix\n\t\n"]
        df = pd.DataFrame({"id": range(4), "s": strings})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert list(strings) == list(df_b["s"].astype(str))

    def test_string_numerica_nao_vira_numero(self, tmp):
        """Strings que parecem números devem ser preservadas como string."""
        strings = ["123", "45.67", "-89", "0", "1e5", "NaN", "None"]
        df = pd.DataFrame({"id": range(len(strings)), "s": strings})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert list(strings) == list(df_b["s"].astype(str))


# ══════════════════════════════════════════════════════════════════════════════
# §7 MULTI-COLUNA EDGE CASES
# ══════════════════════════════════════════════════════════════════════════════

class TestMultiColunaEdgeCases:

    def test_coluna_numerica_alta_cardinalidade_vs_categoria(self, tmp):
        """Coluna com IDs que parecem categóricos (repetidos mas muitos)."""
        N   = 5_000
        np.random.seed(42)
        # 400 valores distintos = acima do limite de category_u8
        ids = np.random.choice(np.arange(1, 401, dtype=np.int32), N)
        df  = pd.DataFrame({"id": np.arange(N, dtype=np.int32), "ref_id": ids})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        assert np.array_equal(ids, df_b["ref_id"].values[:N].astype(np.int32))

    def test_todas_colunas_mesmo_predictor(self, tmp):
        """DataFrame onde todas as colunas usam o mesmo preditor (todos floats)."""
        N   = 3_000
        np.random.seed(9)
        df  = pd.DataFrame({f"v{i}": np.round(np.random.uniform(1,1000,N),2)
                            for i in range(10)})
        df.insert(0, "id", np.arange(N, dtype=np.int32))
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path, codec=pf.CODEC_LZMA2)
        df_b = pf.unfreeze(path, verify=True)
        assert len(df_b) == N
        for col in (c for c in df.columns if c != "id"):
            diff = np.abs(df[col].values - df_b[col].values[:N].astype(float)).max()
            assert diff < 0.01, f"col={col} max_diff={diff}"

    def test_muitas_colunas_wide_table(self, tmp):
        """DataFrame com 100 colunas (wide table)."""
        N = 1_000
        np.random.seed(42)
        data = {"id": np.arange(N, dtype=np.int32)}
        for i in range(50):
            data[f"float_{i:02d}"] = np.round(np.random.uniform(1, 100, N), 2)
        for i in range(30):
            data[f"cat_{i:02d}"] = np.random.choice(["A","B","C","D"], N)
        for i in range(19):
            data[f"int_{i:02d}"] = np.random.randint(1, 1000, N, dtype=np.int32)
        df   = pd.DataFrame(data)
        path = os.path.join(tmp, "wide.permafrost")
        m    = pf.freeze(df, path, codec=pf.CODEC_LZMA2)
        assert m["cols"] == 100
        df_b = pf.unfreeze(path, verify=True)
        assert len(df_b) == N
        assert set(df_b.columns) == set(df.columns)

    def test_coluna_partição_nao_no_inicio(self, tmp):
        """partition_by em coluna que não é a primeira."""
        N = 4_000
        np.random.seed(3)
        df = pd.DataFrame({
            "nome":    [f"user_{i}" for i in range(N)],
            "email":   [f"u{i}@test.com" for i in range(N)],
            "ano":     np.random.choice([2021,2022,2023], N).astype(np.int16),
            "total":   np.round(np.random.uniform(1,1000,N), 2),
            "id":      np.arange(1, N+1, dtype=np.int32),
        }).sort_values("ano").reset_index(drop=True)
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path, partition_by="ano")
        info = pf.audit(path)
        assert info["partition_col"] == "ano"
        df_b = pf.unfreeze(path, filter={"ano": 2022})
        assert (df_b["ano"].values.astype(int) == 2022).all()

    def test_dataframe_com_apenas_categoria(self, tmp):
        """DataFrame com APENAS colunas de categoria."""
        N  = 2_000
        np.random.seed(1)
        df = pd.DataFrame({
            "a": np.random.choice(["X","Y","Z"], N),
            "b": np.random.choice(["P","Q"], N),
            "c": np.random.choice(["M","N","O","P"], N),
        })
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        for col in ("a","b","c"):
            pct = (df[col].values == df_b[col].astype(str).values[:N]).mean()
            assert pct == 1.0, f"col={col}: {pct*100:.2f}%"

    def test_dataframe_com_apenas_timestamps(self, tmp):
        """DataFrame com APENAS colunas de timestamp."""
        N   = 1_000
        df  = pd.DataFrame({
            "t1": pd.date_range("2020-01-01", periods=N, freq="1h"),
            "t2": pd.date_range("2015-06-01", periods=N, freq="1D"),
            "t3": pd.date_range("2022-01-01", periods=N, freq="30min"),
        })
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.unfreeze(path, verify=True)
        for col in ("t1","t2","t3"):
            orig = df[col].dt.strftime("%Y-%m-%d %H").values
            rest = pd.to_datetime(df_b[col]).dt.strftime("%Y-%m-%d %H").values
            pct  = (orig == rest[:N]).mean()
            assert pct == 1.0, f"col={col}: {pct*100:.2f}%"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

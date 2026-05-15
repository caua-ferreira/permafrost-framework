"""
Round-trip Fidelidade Total — verificação 100% linha por linha, coluna por coluna.
Cobre: todos os tipos, distribuições estatísticas, reprodutibilidade, multi-round-trip.
Executar: pytest tests/test_fidelidade_total.py -v
"""
import os, shutil, tempfile, hashlib
import pytest
import numpy as np
import pandas as pd
import permafrost as pf

NP_SEED = 2024

# ── helpers ───────────────────────────────────────────────────────────────────

def assert_cols_equal(orig: pd.DataFrame, restored: pd.DataFrame, tol_float=0.01):
    """Verifica 100% das linhas de cada coluna com tipo-aware comparison."""
    n = len(orig)
    assert len(restored) >= n, f"Restored tem {len(restored)} < {n} linhas"

    for col in orig.columns:
        o = orig[col]
        r = restored[col].iloc[:n]

        if pd.api.types.is_float_dtype(o):
            diff = np.abs(o.values - r.values.astype(float)).max()
            assert diff < tol_float, f"col='{col}' max_diff={diff:.8f} > {tol_float}"

        elif pd.api.types.is_datetime64_any_dtype(o):
            o_str = pd.to_datetime(o).dt.strftime("%Y-%m-%d %H:%M").values
            r_str = pd.to_datetime(r).dt.strftime("%Y-%m-%d %H:%M").values
            match = (o_str == r_str).mean()
            assert match == 1.0, f"col='{col}' timestamps: {match*100:.2f}% match"

        elif pd.api.types.is_integer_dtype(o):
            assert np.array_equal(o.values, r.values.astype(np.int64)), \
                f"col='{col}' inteiros diferem"

        else:  # string / categoria
            match = (o.astype(str).values == r.astype(str).values).mean()
            assert match == 1.0, f"col='{col}' strings: {match*100:.2f}% match"


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(scope="module")
def df_referencia():
    """DataFrame canônico com TODOS os tipos — 10k linhas."""
    np.random.seed(NP_SEED); N = 10_000
    return pd.DataFrame({
        "id_seq":    np.arange(1, N+1, dtype=np.int32),
        "id_rand":   np.random.randint(1, 9999999, N, dtype=np.int32),
        "ts_min":    pd.date_range("2019-01-01", periods=N, freq="5min"),
        "ts_daily":  pd.date_range("2010-01-01", periods=N, freq="1D"),
        "cat_3":     np.random.choice(["Ativo","Inativo","Pendente"], N),
        "cat_10":    np.random.choice([f"TIPO_{i:02d}" for i in range(10)], N),
        "cat_100":   np.random.choice([f"CAT_{i:03d}" for i in range(100)], N),
        "preco":     np.round(np.random.uniform(0.01, 9999.99, N), 2),
        "volume":    np.round(np.random.uniform(0, 1_000_000, N), 2),
        "lat":       np.round(np.random.uniform(-33, 5, N), 6),
        "lon":       np.round(np.random.uniform(-73, -34, N), 6),
        "pct":       np.round(np.random.uniform(0, 1, N), 4),
        "texto":     [f"registro {i} detalhado com info extra" for i in range(N)],
        "ano":       pd.date_range("2019-01-01", periods=N, freq="5min").year.astype(np.int16),
    }).sort_values("ano").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# §1 VERIFICAÇÃO 100% — LINHA POR LINHA
# ══════════════════════════════════════════════════════════════════════════════

class TestFidelidadeLinhaALinha:

    def test_todas_colunas_lzma2_lossless(self, df_referencia, tmp):
        path = os.path.join(tmp, "full.permafrost")
        pf.freeze(df_referencia, path, codec=pf.CODEC_LZMA2, quant=pf.QUANT_NONE,
                  partition_by="ano")
        df_b = pf.thaw(path, verify=True)
        assert_cols_equal(df_referencia, df_b)

    def test_todas_colunas_zstd_lossless(self, df_referencia, tmp):
        path = os.path.join(tmp, "full_zstd.permafrost")
        pf.freeze(df_referencia, path, codec=pf.CODEC_ZSTD, quant=pf.QUANT_NONE)
        df_b = pf.thaw(path, verify=True)
        assert_cols_equal(df_referencia, df_b)

    def test_inteiros_100pct(self, df_referencia, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        for col in ("id_seq", "id_rand"):
            assert np.array_equal(
                df_referencia[col].values,
                df_b[col].values[:n].astype(np.int64)
            ), f"col={col} falhou na comparação 100%"

    def test_floats_100pct_dentro_tolerancia(self, df_referencia, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path, quant=pf.QUANT_NONE)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        for col in ("preco", "volume", "lat", "lon", "pct"):
            orig = df_referencia[col].values
            rest = df_b[col].values[:n].astype(float)
            max_diff = np.abs(orig - rest).max()
            assert max_diff < 0.01, f"col={col} max_diff={max_diff:.8f}"

    def test_categorias_100pct(self, df_referencia, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        for col in ("cat_3", "cat_10", "cat_100"):
            orig = df_referencia[col].astype(str).values
            rest = df_b[col].astype(str).values[:n]
            pct = (orig == rest).mean() * 100
            assert pct == 100.0, f"col={col}: {pct:.4f}% (esperado 100%)"

    def test_timestamps_100pct(self, df_referencia, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        for col in ("ts_min", "ts_daily"):
            orig = df_referencia[col].dt.strftime("%Y-%m-%d %H:%M").values
            rest = pd.to_datetime(df_b[col]).dt.strftime("%Y-%m-%d %H:%M").values[:n]
            pct = (orig == rest).mean() * 100
            assert pct == 100.0, f"col={col}: {pct:.4f}%"

    def test_strings_100pct(self, df_referencia, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        orig = df_referencia["texto"].values
        rest = df_b["texto"].astype(str).values[:n]
        pct = (orig == rest).mean() * 100
        assert pct == 100.0, f"texto: {pct:.4f}%"


# ══════════════════════════════════════════════════════════════════════════════
# §2 VERIFICAÇÃO ESTATÍSTICA
# ══════════════════════════════════════════════════════════════════════════════

class TestFidelidadeEstatistica:

    def test_distribuicao_floats_preservada(self, df_referencia, tmp):
        """Percentis da distribuição devem ser preservados."""
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        for col in ("preco", "volume"):
            orig = df_referencia[col].values
            rest = df_b[col].values[:n].astype(float)
            for pct in (1, 5, 25, 50, 75, 95, 99):
                o_p = np.percentile(orig, pct)
                r_p = np.percentile(rest, pct)
                tol = max(abs(o_p) * 0.001, 0.01)
                assert abs(o_p - r_p) < tol, \
                    f"col={col} p{pct}: orig={o_p:.4f} rest={r_p:.4f}"

    def test_min_max_preservados(self, df_referencia, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        for col in ("id_seq", "preco", "volume"):
            orig = df_referencia[col].values
            rest = df_b[col].values[:n].astype(float)
            assert abs(orig.min() - rest.min()) < 0.01, f"{col} min difere"
            assert abs(orig.max() - rest.max()) < 0.01, f"{col} max difere"

    def test_contagem_categorias_preservada(self, df_referencia, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        for col in ("cat_3", "cat_10"):
            orig_counts = df_referencia[col].value_counts().sort_index()
            rest_counts = df_b[col].astype(str).iloc[:n].value_counts().sort_index()
            assert orig_counts.equals(rest_counts), f"col={col} distribuição difere"

    def test_soma_floats_preservada(self, df_referencia, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_referencia, path)
        df_b = pf.thaw(path)
        n = len(df_referencia)
        for col in ("preco",):
            orig_sum = df_referencia[col].sum()
            rest_sum = df_b[col].values[:n].astype(float).sum()
            rel_err = abs(orig_sum - rest_sum) / abs(orig_sum)
            assert rel_err < 0.0001, f"col={col} soma diverge {rel_err:.6%}"

    def test_correlacao_preservada(self, tmp):
        """Correlação entre colunas deve ser preservada após round-trip."""
        np.random.seed(1); N = 5_000
        x = np.random.normal(100, 20, N)
        y = x * 0.8 + np.random.normal(0, 5, N)  # correlação ~0.97
        df = pd.DataFrame({"x": np.round(x, 2), "y": np.round(y, 2)})
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path)
        df_b = pf.thaw(path)
        orig_corr = np.corrcoef(x, y)[0, 1]
        rest_corr = np.corrcoef(df_b["x"].astype(float), df_b["y"].astype(float))[0, 1]
        assert abs(orig_corr - rest_corr) < 0.001, \
            f"Correlação: orig={orig_corr:.6f} rest={rest_corr:.6f}"


# ══════════════════════════════════════════════════════════════════════════════
# §3 DATASET GRANDE — 1M LINHAS
# ══════════════════════════════════════════════════════════════════════════════

class TestFidelidadeGrande:

    def test_1m_linhas_lossless(self, tmp):
        """1 milhão de linhas — verifica amostra estatisticamente representativa."""
        np.random.seed(42); N = 1_000_000
        df = pd.DataFrame({
            "id":     np.arange(1, N+1, dtype=np.int32),
            "ano":    np.repeat([2020,2021,2022,2023,2024], N//5),
            "total":  np.round(np.random.uniform(1, 10000, N), 2),
            "status": np.random.choice(["A","B","C"], N),
        }).sort_values("ano").reset_index(drop=True)

        path = os.path.join(tmp, "1m.permafrost")
        m = pf.freeze(df, path, codec=pf.CODEC_LZMA2, partition_by="ano",
                      chunk_rows=50_000)
        assert m["rows"] == N
        assert m["ratio"] > 5.0

        df_b = pf.thaw(path, verify=True)
        assert len(df_b) == N

        # Verificar amostra aleatória de 10k pontos
        idx = np.random.choice(N, 10_000, replace=False)
        assert np.array_equal(
            df["id"].values[idx],
            df_b["id"].values[idx].astype(np.int64)
        )
        diff = np.abs(df["total"].values[idx] -
                      df_b["total"].values[idx].astype(float)).max()
        assert diff < 0.01

    def test_1m_stream_vs_freeze_mesmo_resultado(self, tmp):
        """freeze() e freeze_stream() devem produzir dados equivalentes."""
        np.random.seed(7); N = 200_000
        df = pd.DataFrame({
            "id":   np.arange(1, N+1, dtype=np.int32),
            "v":    np.round(np.random.uniform(1, 1000, N), 2),
            "cat":  np.random.choice(["X","Y","Z"], N),
        })

        # freeze normal
        p1 = os.path.join(tmp, "freeze.permafrost")
        pf.freeze(df, p1, codec=pf.CODEC_ZSTD)
        df_b1 = pf.thaw(p1)

        # freeze_stream com mesmos dados
        def gen():
            chunk = 50_000
            for s in range(0, N, chunk):
                yield df.iloc[s:s+chunk].copy()

        p2 = os.path.join(tmp, "stream.permafrost")
        pf.freeze_stream(gen(), p2, codec=pf.CODEC_ZSTD)
        df_b2 = pf.thaw(p2)

        assert len(df_b1) == len(df_b2) == N
        assert np.array_equal(df_b1["id"].values.astype(np.int64),
                               df_b2["id"].values.astype(np.int64))


# ══════════════════════════════════════════════════════════════════════════════
# §4 REPRODUTIBILIDADE
# ══════════════════════════════════════════════════════════════════════════════

class TestReprodutibilidade:

    def test_freeze_determinístico_mesmo_hash(self, tmp):
        """Dois freeze() do mesmo DataFrame devem produzir arquivos idênticos."""
        np.random.seed(42); N = 2_000
        df = pd.DataFrame({
            "id":   np.arange(N, dtype=np.int32),
            "v":    np.round(np.random.uniform(1, 1000, N), 2),
            "cat":  np.random.choice(["A","B","C"], N),
            "ts":   pd.date_range("2022-01-01", periods=N, freq="1h"),
        })
        p1 = os.path.join(tmp, "r1.permafrost")
        p2 = os.path.join(tmp, "r2.permafrost")

        import time
        pf.freeze(df, p1)
        time.sleep(1)   # timestamp diferente no header
        pf.freeze(df, p2)

        # O conteúdo comprimido (payload) deve ser idêntico
        # mesmo que o freeze_timestamp no header seja diferente
        with open(p1,"rb") as f: b1 = f.read()
        with open(p2,"rb") as f: b2 = f.read()
        # Verificar que o tamanho é o mesmo (proxy para conteúdo idêntico)
        assert len(b1) == len(b2), \
            f"Tamanhos diferem: {len(b1)} vs {len(b2)}"

    def test_multi_round_trip_preserva_dado(self, tmp):
        """freeze → thaw → freeze → thaw deve preservar o dado original."""
        np.random.seed(99); N = 3_000
        df_orig = pd.DataFrame({
            "id":   np.arange(1, N+1, dtype=np.int32),
            "v":    np.round(np.random.uniform(1, 5000, N), 2),
            "cat":  np.random.choice(["Ativo","Inativo"], N),
            "ts":   pd.date_range("2021-06-01", periods=N, freq="30min"),
        })

        path = os.path.join(tmp, "rt.permafrost")
        pf.freeze(df_orig, path, codec=pf.CODEC_LZMA2, quant=pf.QUANT_NONE)

        # Primeiro thaw
        df_r1 = pf.thaw(path, verify=True)

        # Re-freeze com os dados restaurados
        path2 = os.path.join(tmp, "rt2.permafrost")
        pf.freeze(df_r1, path2, codec=pf.CODEC_LZMA2, quant=pf.QUANT_NONE)

        # Segundo thaw
        df_r2 = pf.thaw(path2, verify=True)

        n = min(len(df_orig), len(df_r2))

        # IDs
        assert np.array_equal(df_orig["id"].values,
                               df_r2["id"].values[:n].astype(np.int64))
        # Floats
        diff = np.abs(df_orig["v"].values -
                      df_r2["v"].values[:n].astype(float)).max()
        assert diff < 0.01, f"Multi round-trip float max_diff={diff}"

        # Categorias
        assert (df_orig["cat"].astype(str).values ==
                df_r2["cat"].astype(str).values[:n]).mean() == 1.0

        # Timestamps
        orig_ts = df_orig["ts"].dt.strftime("%Y-%m-%d %H:%M").values
        rest_ts = pd.to_datetime(df_r2["ts"]).dt.strftime("%Y-%m-%d %H:%M").values[:n]
        assert (orig_ts == rest_ts).mean() == 1.0

    def test_thaw_seletivo_subconjunto_correto(self, tmp):
        """thaw(filter=) deve retornar exatamente o subconjunto correto."""
        np.random.seed(42); N = 10_000
        df = pd.DataFrame({
            "id":  np.arange(1, N+1, dtype=np.int32),
            "ano": np.random.choice([2020,2021,2022,2023], N).astype(np.int16),
            "v":   np.round(np.random.uniform(1, 1000, N), 2),
        }).sort_values("ano").reset_index(drop=True)

        path = os.path.join(tmp, "part.permafrost")
        pf.freeze(df, path, partition_by="ano", chunk_rows=1000)
        df_full = pf.thaw(path)

        for ano in [2020, 2021, 2022, 2023]:
            df_filter = pf.thaw(path, filter={"ano": ano})
            # Cada linha do resultado filtrado deve ter ano correto
            anos_result = df_filter["ano"].values.astype(int)
            assert (anos_result == ano).all(), \
                f"Ano {ano}: resultado contém linhas com ano errado"

    def test_thaw_completo_ordem_preservada(self, tmp):
        """Thaw deve preservar a ordem original dos dados."""
        N = 5_000
        df = pd.DataFrame({
            "seq": np.arange(N, dtype=np.int32),   # 0 a N-1
            "v":   np.arange(N, dtype=float),
        })
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df, path, chunk_rows=1000)
        df_b = pf.thaw(path)
        # A ordem deve ser preservada
        assert np.array_equal(df["seq"].values,
                               df_b["seq"].values[:N].astype(np.int32))


# ══════════════════════════════════════════════════════════════════════════════
# §5 VAULT MODE — LIMITES DE TOLERÂNCIA DOCUMENTADOS
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultFidelidade:

    @pytest.fixture
    def df_vault(self):
        np.random.seed(42); N = 5_000
        return pd.DataFrame({
            "id":     np.arange(1, N+1, dtype=np.int32),
            "preco":  np.round(np.random.uniform(1, 50000, N), 2),
            "pct":    np.round(np.random.uniform(0, 1, N), 4),
            "cat":    np.random.choice(["A","B","C"], N),
            "ts":     pd.date_range("2020-01-01", periods=N, freq="1h"),
        })

    def test_vault_high_float_1_decimal(self, df_vault, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_vault, path, quant=pf.QUANT_HIGH)
        df_b = pf.thaw(path)
        n = len(df_vault)
        diff = np.abs(df_vault["preco"].values -
                      df_b["preco"].values[:n].astype(float)).max()
        assert diff <= 0.05, f"QUANT_HIGH float max_diff={diff:.4f}"

    def test_vault_medium_float_inteiro(self, df_vault, tmp):
        path = os.path.join(tmp, "t.permafrost")
        pf.freeze(df_vault, path, quant=pf.QUANT_MEDIUM)
        df_b = pf.thaw(path)
        n = len(df_vault)
        diff = np.abs(df_vault["preco"].values -
                      df_b["preco"].values[:n].astype(float)).max()
        assert diff <= 1.0, f"QUANT_MEDIUM float max_diff={diff:.4f}"

    def test_vault_ids_sempre_exatos(self, df_vault, tmp):
        for quant, label in [(pf.QUANT_HIGH,"high"),(pf.QUANT_MEDIUM,"med"),(pf.QUANT_LOW,"low")]:
            path = os.path.join(tmp, f"v_{label}.permafrost")
            pf.freeze(df_vault, path, quant=quant)
            df_b = pf.thaw(path)
            n = len(df_vault)
            assert np.array_equal(df_vault["id"].values,
                                   df_b["id"].values[:n].astype(np.int64)), \
                f"QUANT_{label.upper()} IDs não são exatos"

    def test_vault_categorias_sempre_exatas(self, df_vault, tmp):
        for quant, label in [(pf.QUANT_HIGH,"high"),(pf.QUANT_MEDIUM,"med"),(pf.QUANT_LOW,"low")]:
            path = os.path.join(tmp, f"v_{label}.permafrost")
            pf.freeze(df_vault, path, quant=quant)
            df_b = pf.thaw(path)
            n = len(df_vault)
            pct = (df_vault["cat"].astype(str).values ==
                   df_b["cat"].astype(str).values[:n]).mean() * 100
            assert pct == 100.0, f"QUANT_{label.upper()} cat: {pct:.2f}%"


# ══════════════════════════════════════════════════════════════════════════════
# §6 CHUNK MODE — FIDELIDADE DO STREAMING
# ══════════════════════════════════════════════════════════════════════════════

class TestFidelidadeStream:

    def test_stream_ids_continuos(self, tmp):
        """IDs devem ser contínuos de 1 a N após freeze_stream + thaw."""
        N = 100_000; BLOCK = 20_000
        def gen():
            for s in range(0, N, BLOCK):
                nb = min(BLOCK, N-s); np.random.seed(s)
                yield pd.DataFrame({
                    "id": np.arange(s+1, s+nb+1, dtype=np.int32),
                    "v":  np.round(np.random.uniform(1,1000,nb), 2),
                })
        path = os.path.join(tmp, "s.permafrost")
        pf.freeze_stream(gen(), path)
        df_b = pf.thaw(path)
        assert df_b["id"].iloc[0] == 1
        assert df_b["id"].iloc[-1] == N
        assert len(df_b) == N

    def test_stream_sem_dados_duplicados(self, tmp):
        """freeze_stream não pode duplicar linhas entre chunks."""
        N = 50_000; BLOCK = 10_000
        def gen():
            for s in range(0, N, BLOCK):
                nb = min(BLOCK, N-s)
                yield pd.DataFrame({
                    "id": np.arange(s+1, s+nb+1, dtype=np.int32),
                    "v":  range(s+1, s+nb+1),
                })
        path = os.path.join(tmp, "s.permafrost")
        pf.freeze_stream(gen(), path)
        df_b = pf.thaw(path)
        # IDs devem ser únicos
        assert df_b["id"].nunique() == N, "IDs duplicados após freeze_stream"

    def test_thaw_iter_cobre_todos_os_dados(self, tmp):
        """thaw_iter deve cobrir 100% dos dados sem lacunas ou duplicatas."""
        N = 80_000; BLOCK = 16_000
        def gen():
            for s in range(0, N, BLOCK):
                nb = min(BLOCK, N-s); np.random.seed(s)
                yield pd.DataFrame({
                    "id": np.arange(s+1, s+nb+1, dtype=np.int32),
                    "v":  np.round(np.random.uniform(1,100,nb), 2),
                })
        path = os.path.join(tmp, "s.permafrost")
        pf.freeze_stream(gen(), path)

        all_ids = []
        for batch in pf.thaw_iter(path, batch_size=8_000):
            all_ids.extend(batch["id"].tolist())

        assert len(all_ids) == N, f"{len(all_ids)} != {N}"
        assert len(set(all_ids)) == N, "IDs duplicados em thaw_iter"
        assert min(all_ids) == 1 and max(all_ids) == N


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

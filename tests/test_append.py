"""
Testes para pf.append() / pf.freeze_append()
=============================================
Executar: pytest tests/test_append.py -v
"""
import os
import shutil
import tempfile

import numpy as np
import pandas as pd
import pytest

import permafrost as pf


# ─────────────────────────── fixtures ────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(scope="module")
def base_df():
    np.random.seed(42)
    N = 2000
    return pd.DataFrame({
        "id":     np.arange(1, N + 1, dtype=np.int32),
        "regiao": np.random.choice(["Norte", "Sul", "Leste", "Oeste"], N),
        "ano":    np.random.choice([2021, 2022, 2023], N).astype(np.int16),
        "valor":  np.round(np.random.uniform(10, 5000, N), 2),
    })


@pytest.fixture(scope="module")
def extra_df():
    np.random.seed(99)
    N = 500
    return pd.DataFrame({
        "id":     np.arange(2001, 2001 + N, dtype=np.int32),
        "regiao": np.random.choice(["Norte", "Sul", "Leste", "Oeste"], N),
        "ano":    np.random.choice([2023, 2024], N).astype(np.int16),
        "valor":  np.round(np.random.uniform(10, 5000, N), 2),
    })


# ─────────────── API básica ───────────────────────────────────────────────────

class TestAppendBasic:
    def test_append_alias_exists(self):
        assert pf.append is pf.freeze_append

    def test_append_returns_dict(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        result = pf.append(path, extra_df)
        assert "appended_rows" in result
        assert "total_rows" in result
        assert "total_chunks" in result
        assert "append_s" in result

    def test_append_correct_row_counts(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        result = pf.append(path, extra_df)
        assert result["appended_rows"] == len(extra_df)
        assert result["total_rows"] == len(base_df) + len(extra_df)

    def test_append_data_retrievable(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        pf.append(path, extra_df)
        df = pf.unfreeze(path)
        assert len(df) == len(base_df) + len(extra_df)
        assert set(df.columns) == set(base_df.columns)

    def test_append_base_data_intact(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        pf.append(path, extra_df)
        df = pf.unfreeze(path)
        # IDs 1..2000 devem estar todos presentes
        assert set(base_df["id"].tolist()).issubset(set(df["id"].tolist()))

    def test_append_pf_extension(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.pf")
        pf.freeze(base_df, path)
        pf.append(path, extra_df)
        df = pf.unfreeze(path)
        assert len(df) == len(base_df) + len(extra_df)


# ─────────────── audit após append ───────────────────────────────────────────

class TestAppendAudit:
    def test_audit_total_rows_updated(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        pf.append(path, extra_df)
        info = pf.audit(path)
        assert info["orig_rows"] == len(base_df) + len(extra_df)

    def test_audit_n_chunks_updated(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path, chunk_rows=500)
        info_before = pf.audit(path)
        pf.append(path, extra_df)
        info_after = pf.audit(path)
        assert info_after["n_chunks"] > info_before["n_chunks"]

    def test_audit_columns_unchanged(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        pf.append(path, extra_df)
        info = pf.audit(path)
        assert set(info["columns"]) == set(base_df.columns)


# ─────────────── particionamento ─────────────────────────────────────────────

class TestAppendPartition:
    def test_append_partition_filter_works(self, base_df, extra_df, tmp_dir):
        """Filtro por partition_by deve funcionar sobre dados base + appended."""
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path, partition_by="ano")
        pf.append(path, extra_df)
        # 2024 só existe nos dados do append
        df_2024 = pf.unfreeze(path, filter={"ano": 2024})
        assert set(df_2024["ano"].unique()) == {2024}
        assert len(df_2024) == len(extra_df[extra_df["ano"] == 2024])

    def test_append_multi_col_partition(self, base_df, extra_df, tmp_dir):
        """Append em arquivo com partition_by multi-coluna."""
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=500)
        pf.append(path, extra_df)
        df = pf.unfreeze(path)
        assert len(df) == len(base_df) + len(extra_df)

    def test_append_multi_col_filter_after_append(self, base_df, extra_df, tmp_dir):
        """Filtro por segunda coluna de partição funciona após append."""
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=500)
        pf.append(path, extra_df)
        df_sul = pf.unfreeze(path, filter={"regiao": "Sul"})
        assert set(df_sul["regiao"].unique()) == {"Sul"}
        assert len(df_sul) > 0

    def test_append_no_partition(self, base_df, extra_df, tmp_dir):
        """Arquivo sem partition_by funciona normalmente com append."""
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        pf.append(path, extra_df)
        df = pf.unfreeze(path)
        assert len(df) == len(base_df) + len(extra_df)


# ─────────────── peek após append ────────────────────────────────────────────

class TestAppendPeek:
    def test_peek_yields_all_after_append(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path, partition_by="ano", chunk_rows=500)
        pf.append(path, extra_df)
        frames = list(pf.peek(path))
        total = sum(len(f) for f in frames)
        assert total == len(base_df) + len(extra_df)

    def test_peek_filter_after_append(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path, partition_by="ano")
        pf.append(path, extra_df)
        frames = list(pf.peek(path, filter={"ano": 2024}))
        assert len(frames) > 0
        result = pd.concat(frames, ignore_index=True)
        assert set(result["ano"].unique()) == {2024}


# ─────────────── query SQL após append ───────────────────────────────────────

class TestAppendQuery:
    def test_query_count_after_append(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        pf.append(path, extra_df)
        df = pf.query(f"SELECT COUNT(*) AS n FROM '{path}'")
        assert df["n"].iloc[0] == len(base_df) + len(extra_df)

    def test_query_filter_after_append(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path, partition_by="ano")
        pf.append(path, extra_df)
        # ano=2024 só existe em extra_df
        df = pf.query(f"SELECT * FROM '{path}' WHERE ano = 2024")
        assert len(df) == len(extra_df[extra_df["ano"] == 2024])


# ─────────────── múltiplos appends ───────────────────────────────────────────

class TestMultipleAppends:
    def test_three_appends_total_rows(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        np.random.seed(10)
        batch1 = base_df.iloc[:500].copy()
        batch2 = base_df.iloc[500:1000].copy()
        batch3 = base_df.iloc[1000:].copy()
        pf.freeze(batch1, path)
        pf.append(path, batch2)
        pf.append(path, batch3)
        df = pf.unfreeze(path)
        assert len(df) == len(base_df)

    def test_append_returns_cumulative_total(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        half = len(base_df) // 2
        pf.freeze(base_df.iloc[:half], path)
        r = pf.append(path, base_df.iloc[half:])
        assert r["total_rows"] == len(base_df)


# ─────────────── erros esperados ─────────────────────────────────────────────

class TestAppendErrors:
    def test_schema_mismatch_raises(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        df_bad = base_df.copy()
        df_bad = df_bad.drop(columns=["regiao"])
        with pytest.raises(ValueError, match="[Ss]chema"):
            pf.append(path, df_bad)

    def test_extra_column_raises(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        df_bad = base_df.copy()
        df_bad["nova_coluna"] = 0
        with pytest.raises(ValueError, match="[Ss]chema"):
            pf.append(path, df_bad)

    def test_verify_sha_catches_corruption(self, base_df, extra_df, tmp_dir):
        path = os.path.join(tmp_dir, "v.permafrost")
        pf.freeze(base_df, path)
        # corromper byte no meio dos chunks
        # Read header to find payload start, then corrupt first chunk bytes
        import sys; sys.path.insert(0, '/tmp/pf_work/src')
        from permafrost.codec import _read_header
        with open(path, 'rb') as fh:
            raw = fh.read()
        h = _read_header(raw)
        corrupt_offset = h['payload_start'] + 10
        with open(path, 'r+b') as f:
            f.seek(corrupt_offset)
            f.write(b'\xff\xff\xff\xff')
        with pytest.raises((ValueError, Exception)):
            pf.append(path, extra_df, verify=True)


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v", "--tb=short"])

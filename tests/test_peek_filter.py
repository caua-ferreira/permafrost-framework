"""
Testes para o filtro avançado em peek() e unfreeze().

Cobre:
- Exact match (sem falso positivo de substring)
- Lista de valores (OR)
- Range via 2-tuple
- Multi-coluna AND
- Coluna não-particionada (post-filter após descompressão)
- Integração: peek com batch_size + filtro

Executar: pytest tests/test_peek_filter.py -v
"""
import os
import shutil
import tempfile

import numpy as np
import pandas as pd
import pytest

import permafrost as pf
from permafrost.codec import _chunk_matches_value, _partition_filter, _column_filter


# ─────────────────────────── fixtures ────────────────────────────────────────

@pytest.fixture(scope="module")
def base_df():
    np.random.seed(0)
    N = 15_000
    dates = pd.date_range("2019-01-01", periods=N, freq="6h")
    return pd.DataFrame({
        "id":     np.arange(1, N + 1, dtype=np.int32),
        "data":   dates,
        "ano":    dates.year.astype(np.int16),
        "mes":    dates.month.astype(np.int8),
        "regiao": np.random.choice(["Norte", "Sul", "Leste", "Oeste"], N),
        "valor":  np.round(np.random.uniform(10, 9_999, N), 2),
    }).sort_values("ano").reset_index(drop=True)


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(scope="module")
def frozen_file(base_df, tmp_path_factory):
    path = str(tmp_path_factory.mktemp("pf") / "test.permafrost")
    pf.freeze(base_df, path, partition_by="ano", chunk_rows=1000)
    return path


# ─────────────── unit tests: _chunk_matches_value ────────────────────────────

class TestChunkMatchesValue:
    def test_exact_single(self):
        assert _chunk_matches_value("2022", "2022") is True

    def test_exact_no_false_positive(self):
        # old substring bug: "2" in "2022" → True  (wrong)
        assert _chunk_matches_value("2022", "2") is False

    def test_exact_no_false_positive_leading(self):
        assert _chunk_matches_value("2022", "202") is False

    def test_range_contains(self):
        assert _chunk_matches_value("2021-2023", "2022") is True

    def test_range_boundary_lo(self):
        assert _chunk_matches_value("2021-2023", "2021") is True

    def test_range_boundary_hi(self):
        assert _chunk_matches_value("2021-2023", "2023") is True

    def test_range_outside(self):
        assert _chunk_matches_value("2021-2023", "2024") is False


# ─────────────── unit tests: _partition_filter ───────────────────────────────

class TestPartitionFilter:
    def _make_index(self):
        return [
            {"chunk_id": 0, "part_col": "ano", "part_key": "2019",
             "byte_offset": 0, "byte_len": 100, "row_start": 0, "row_end": 999, "sha256": "x"},
            {"chunk_id": 1, "part_col": "ano", "part_key": "2020",
             "byte_offset": 100, "byte_len": 100, "row_start": 1000, "row_end": 1999, "sha256": "x"},
            {"chunk_id": 2, "part_col": "ano", "part_key": "2021",
             "byte_offset": 200, "byte_len": 100, "row_start": 2000, "row_end": 2999, "sha256": "x"},
            {"chunk_id": 3, "part_col": "ano", "part_key": "2022",
             "byte_offset": 300, "byte_len": 100, "row_start": 3000, "row_end": 3999, "sha256": "x"},
        ]

    def test_exact_single_value(self):
        idx = self._make_index()
        sel = _partition_filter(idx, {"ano": 2021})
        assert len(sel) == 1
        assert sel[0]["part_key"] == "2021"

    def test_exact_no_substring_match(self):
        # "2" should NOT match "2019", "2020", "2021", "2022".
        # _partition_filter uses a safe fallback: when nothing matches it keeps all
        # entries (to avoid data loss), and relies on _column_filter to return empty.
        idx = self._make_index()
        sel = _partition_filter(idx, {"ano": 2})
        # No entry has part_key == "2", so fallback keeps all entries
        assert len(sel) == len(idx)

    def test_list_of_values(self):
        idx = self._make_index()
        sel = _partition_filter(idx, {"ano": [2020, 2022]})
        keys = {e["part_key"] for e in sel}
        assert keys == {"2020", "2022"}

    def test_range_tuple(self):
        idx = self._make_index()
        sel = _partition_filter(idx, {"ano": (2020, 2021)})
        keys = {e["part_key"] for e in sel}
        assert keys == {"2020", "2021"}

    def test_non_partition_col_keeps_all(self):
        # "regiao" is not a partition col → all entries kept
        idx = self._make_index()
        sel = _partition_filter(idx, {"regiao": "Norte"})
        assert len(sel) == len(idx)

    def test_empty_filter_returns_all(self):
        idx = self._make_index()
        assert _partition_filter(idx, {}) == idx


# ─────────────── unit tests: _column_filter ──────────────────────────────────

class TestColumnFilter:
    def _df(self):
        return pd.DataFrame({
            "ano":    [2020, 2021, 2021, 2022, 2022, 2022],
            "regiao": ["Norte", "Sul", "Norte", "Leste", "Norte", "Sul"],
            "valor":  [100.0, 200.0, 150.0, 300.0, 250.0, 180.0],
        })

    def test_exact(self):
        df = _column_filter(self._df(), {"ano": 2021})
        assert list(df["ano"].unique()) == [2021]
        assert len(df) == 2

    def test_list(self):
        df = _column_filter(self._df(), {"ano": [2020, 2022]})
        assert set(df["ano"].unique()) == {2020, 2022}

    def test_range(self):
        df = _column_filter(self._df(), {"ano": (2020, 2021)})
        assert set(df["ano"].unique()) == {2020, 2021}

    def test_multi_col_and(self):
        df = _column_filter(self._df(), {"ano": 2022, "regiao": "Norte"})
        assert len(df) == 1
        assert df.iloc[0]["regiao"] == "Norte"
        assert df.iloc[0]["ano"] == 2022

    def test_unknown_col_ignored(self):
        df = _column_filter(self._df(), {"ano": 2021, "desconhecida": "X"})
        assert len(df) == 2  # only "ano" filter applied

    def test_empty_df(self):
        df = _column_filter(pd.DataFrame(columns=["ano"]), {"ano": 2021})
        assert df.empty


# ─────────────── integration: unfreeze ───────────────────────────────────────

class TestUnfreezeFilter:
    def test_exact_match(self, frozen_file, base_df):
        anos = sorted(base_df["ano"].unique())
        for ano in anos:
            df = pf.unfreeze(frozen_file, filter={"ano": ano})
            assert set(df["ano"].unique()) == {ano}

    def test_list_of_values(self, frozen_file, base_df):
        anos = sorted(base_df["ano"].unique())[:2]
        df = pf.unfreeze(frozen_file, filter={"ano": anos})
        assert set(df["ano"].unique()) == set(anos)

    def test_range_tuple(self, frozen_file, base_df):
        anos = sorted(base_df["ano"].unique())
        lo, hi = anos[0], anos[1]
        df = pf.unfreeze(frozen_file, filter={"ano": (lo, hi)})
        assert set(df["ano"].unique()).issubset({lo, hi})

    def test_non_partition_col_post_filter(self, frozen_file):
        df = pf.unfreeze(frozen_file, filter={"regiao": "Norte"})
        assert set(df["regiao"].unique()) == {"Norte"}
        assert len(df) > 0

    def test_multi_col_and(self, frozen_file, base_df):
        ano = sorted(base_df["ano"].unique())[0]
        df = pf.unfreeze(frozen_file, filter={"ano": ano, "regiao": "Sul"})
        assert set(df["ano"].unique()) == {ano}
        assert set(df["regiao"].unique()) == {"Sul"}

    def test_no_false_positive_substring(self, frozen_file, base_df):
        # Filtering by "ano": 2 must NOT match 2019, 2020, etc.
        df = pf.unfreeze(frozen_file, filter={"ano": 2})
        assert df.empty

    def test_empty_result_returns_empty_df(self, frozen_file):
        df = pf.unfreeze(frozen_file, filter={"ano": 9999})
        assert df.empty


# ─────────────── integration: peek ───────────────────────────────────────────

class TestPeekFilter:
    def test_exact_match_yields_correct_anos(self, frozen_file, base_df):
        ano = sorted(base_df["ano"].unique())[0]
        frames = list(pf.peek(frozen_file, filter={"ano": ano}))
        result = pd.concat(frames, ignore_index=True)
        assert set(result["ano"].unique()) == {ano}

    def test_list_of_values(self, frozen_file, base_df):
        anos = sorted(base_df["ano"].unique())[:2]
        frames = list(pf.peek(frozen_file, filter={"ano": anos}))
        result = pd.concat(frames, ignore_index=True)
        assert set(result["ano"].unique()) == set(anos)

    def test_range_tuple(self, frozen_file, base_df):
        anos = sorted(base_df["ano"].unique())
        lo, hi = anos[0], anos[1]
        frames = list(pf.peek(frozen_file, filter={"ano": (lo, hi)}))
        result = pd.concat(frames, ignore_index=True)
        assert set(result["ano"].unique()).issubset({lo, hi})

    def test_non_partition_col_post_filter(self, frozen_file):
        frames = list(pf.peek(frozen_file, filter={"regiao": "Oeste"}))
        result = pd.concat(frames, ignore_index=True)
        assert set(result["regiao"].unique()) == {"Oeste"}
        assert len(result) > 0

    def test_multi_col_and(self, frozen_file, base_df):
        ano = sorted(base_df["ano"].unique())[0]
        frames = list(pf.peek(frozen_file, filter={"ano": ano, "regiao": "Leste"}))
        result = pd.concat(frames, ignore_index=True)
        assert set(result["ano"].unique()) == {ano}
        assert set(result["regiao"].unique()) == {"Leste"}

    def test_no_false_positive_substring(self, frozen_file):
        frames = list(pf.peek(frozen_file, filter={"ano": 2}))
        assert frames == []

    def test_batch_size_with_filter(self, frozen_file, base_df):
        ano = sorted(base_df["ano"].unique())[0]
        frames = list(pf.peek(frozen_file, filter={"ano": ano}, batch_size=500))
        for f in frames[:-1]:
            assert len(f) == 500
        total = sum(len(f) for f in frames)
        expected = len(base_df[base_df["ano"] == ano])
        assert total == expected

    def test_peek_without_filter_yields_all(self, frozen_file, base_df):
        frames = list(pf.peek(frozen_file))
        total = sum(len(f) for f in frames)
        assert total == len(base_df)


# ─────────────── multi-column partition_by ───────────────────────────────────

class TestMultiColumnPartition:
    """Testa freeze() com partition_by como lista ou '*'."""

    def test_freeze_list_two_cols(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "mc.permafrost")
        m = pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=1000)
        assert m["partition_cols"] == ["ano", "regiao"]
        assert os.path.exists(path)

    def test_freeze_star_all_cols(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "star.permafrost")
        m = pf.freeze(base_df, path, partition_by="*", chunk_rows=1000)
        assert set(base_df.columns).issubset(set(m["partition_cols"]))

    def test_audit_returns_partition_cols_list(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "mc2.permafrost")
        pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=1000)
        info = pf.audit(path)
        assert "partition_cols" in info
        assert "ano" in info["partition_cols"]
        assert "regiao" in info["partition_cols"]
        # backward-compat single key still present
        assert info["partition_col"] == info["partition_cols"][0]

    def test_audit_single_col_backward_compat(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "sc.permafrost")
        pf.freeze(base_df, path, partition_by="ano", chunk_rows=1000)
        info = pf.audit(path)
        assert info["partition_col"] == "ano"
        assert info["partition_cols"] == ["ano"]

    def test_filter_on_second_partition_col(self, base_df, tmp_dir):
        """Filtrar pela segunda coluna de partição deve usar o sparse index."""
        path = os.path.join(tmp_dir, "mc3.permafrost")
        pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=1000)
        df = pf.unfreeze(path, filter={"regiao": "Norte"})
        assert set(df["regiao"].unique()) == {"Norte"}
        assert len(df) > 0

    def test_filter_multi_col_and_with_list_partition(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "mc4.permafrost")
        pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=1000)
        ano = sorted(base_df["ano"].unique())[0]
        df = pf.unfreeze(path, filter={"ano": ano, "regiao": "Sul"})
        assert set(df["ano"].unique()) == {ano}
        assert set(df["regiao"].unique()) == {"Sul"}

    def test_unfreeze_roundtrip_multi_col(self, base_df, tmp_dir):
        """Todos os dados devem ser recuperados mesmo com multi-col partition."""
        path = os.path.join(tmp_dir, "mc5.permafrost")
        pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=1000)
        df = pf.unfreeze(path)
        assert len(df) == len(base_df)
        assert set(df.columns) == set(base_df.columns)

    def test_peek_with_multi_col_partition(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "mc6.permafrost")
        pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=1000)
        ano = sorted(base_df["ano"].unique())[0]
        frames = list(pf.peek(path, filter={"ano": ano}))
        result = pd.concat(frames, ignore_index=True)
        assert set(result["ano"].unique()) == {ano}

    def test_partition_keys_in_index_entries(self, base_df, tmp_dir):
        path = os.path.join(tmp_dir, "mc7.permafrost")
        pf.freeze(base_df, path, partition_by=["ano", "regiao"], chunk_rows=1000)
        info = pf.audit(path)
        # At least some entries should have partition_keys dict
        entries_with_pk = [e for e in info["index_entries"] if "partition_keys" in e]
        assert len(entries_with_pk) > 0
        first_pk = entries_with_pk[0]["partition_keys"]
        assert "ano" in first_pk
        assert "regiao" in first_pk


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v", "--tb=short"])

"""Tests for C4 — Retry + Resumable Upload."""
import os
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from permafrost.storage import (
    LocalAdapter,
    ResumableUploadError,
    _retry,
    _state_path,
    _load_state,
    _save_state,
    _clear_state,
)


# ── _retry ────────────────────────────────────────────────────────────────────

class TestRetry:
    def test_success_on_first_try(self):
        calls = []
        def fn():
            calls.append(1)
            return 42
        assert _retry(fn) == 42
        assert len(calls) == 1

    def test_returns_fn_result(self):
        assert _retry(lambda: "hello") == "hello"

    def test_retry_on_transient_error(self):
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise OSError("transient")
            return "ok"
        result = _retry(fn, max_retries=3, max_delay=0.001)
        assert result == "ok"
        assert len(calls) == 3

    def test_max_retries_exceeded_raises_ResumableUploadError(self):
        def fn():
            raise OSError("always fails")
        with pytest.raises(ResumableUploadError, match="tentativas"):
            _retry(fn, max_retries=3, max_delay=0.001)

    def test_single_attempt_raises_immediately(self):
        def fn():
            raise ValueError("nope")
        with pytest.raises(ResumableUploadError):
            _retry(fn, max_retries=1, max_delay=0.001)

    def test_exact_retry_count(self):
        calls = []
        def fn():
            calls.append(1)
            raise RuntimeError("x")
        with pytest.raises(ResumableUploadError):
            _retry(fn, max_retries=4, max_delay=0.001)
        assert len(calls) == 4

    def test_does_not_swallow_ResumableUploadError(self):
        def fn():
            raise ResumableUploadError("already a ResumableUploadError")
        with pytest.raises(ResumableUploadError, match="already"):
            _retry(fn, max_retries=3, max_delay=0.001)


# ── state file helpers ────────────────────────────────────────────────────────

class TestStateHelpers:
    def test_state_path_default(self, tmp_path):
        sp = _state_path(str(tmp_path / "file.permafrost"))
        assert sp == str(tmp_path / "file.permafrost.upload_state")

    def test_state_path_custom(self, tmp_path):
        custom = str(tmp_path / "my.state")
        assert _state_path("anything", state_file=custom) == custom

    def test_load_nonexistent_returns_none(self, tmp_path):
        assert _load_state(str(tmp_path / "no_such.json")) is None

    def test_load_invalid_json_returns_none(self, tmp_path):
        sf = tmp_path / "bad.json"
        sf.write_text("{ not json }")
        assert _load_state(str(sf)) is None

    def test_save_and_load_roundtrip(self, tmp_path):
        sf = str(tmp_path / "state.json")
        data = {"bytes_written": 1024, "upload_id": "abc123", "parts": []}
        _save_state(sf, data)
        assert _load_state(sf) == data

    def test_clear_removes_file(self, tmp_path):
        sf = str(tmp_path / "state.json")
        _save_state(sf, {"x": 1})
        _clear_state(sf)
        assert not os.path.exists(sf)

    def test_clear_nonexistent_is_noop(self, tmp_path):
        _clear_state(str(tmp_path / "ghost.json"))  # must not raise


# ── LocalAdapter.upload_resumable ─────────────────────────────────────────────

class TestLocalAdapterResumable:
    def test_full_upload_content_correct(self, tmp_path):
        data = os.urandom(2 * 1024 * 1024)
        src  = tmp_path / "source.permafrost"
        src.write_bytes(data)
        dst  = str(tmp_path / "dest" / "output.permafrost")

        adapter = LocalAdapter(str(tmp_path))
        result  = adapter.upload_resumable(str(src), dst)

        assert Path(dst).read_bytes() == data
        assert result["size_bytes"] == len(data)
        assert result["adapter"] == "local"

    def test_state_file_cleared_after_success(self, tmp_path):
        src = tmp_path / "source.permafrost"
        src.write_bytes(os.urandom(256 * 1024))
        dst = str(tmp_path / "out.permafrost")
        sf  = str(tmp_path / "custom.state")

        LocalAdapter(str(tmp_path)).upload_resumable(str(src), dst, state_file=sf)
        assert not os.path.exists(sf)

    def test_not_resumed_on_first_upload(self, tmp_path):
        src = tmp_path / "s.permafrost"
        src.write_bytes(os.urandom(128 * 1024))
        dst = str(tmp_path / "d.permafrost")

        result = LocalAdapter(str(tmp_path)).upload_resumable(str(src), dst)
        assert result["resumed"] is False

    def test_resume_from_partial_state(self, tmp_path):
        data       = os.urandom(2 * 1024 * 1024)
        chunk_size = 512 * 1024
        src        = tmp_path / "src.permafrost"
        src.write_bytes(data)
        dst        = tmp_path / "dst.permafrost"

        # Simulate interrupted upload: first 512 KB already written
        dst.write_bytes(data[:chunk_size])
        sf       = str(tmp_path / "state.json")
        src_stat = src.stat()
        _save_state(sf, {
            "src_mtime":    src_stat.st_mtime,
            "src_size":     src_stat.st_size,
            "remote_uri":   str(dst),
            "bytes_written": chunk_size,
        })

        result = LocalAdapter(str(tmp_path)).upload_resumable(
            str(src), str(dst), chunk_size=chunk_size, state_file=sf
        )

        assert Path(dst).read_bytes() == data
        assert result["resumed"] is True
        assert not os.path.exists(sf)

    def test_changed_source_starts_fresh(self, tmp_path):
        data = os.urandom(1024 * 1024)
        src  = tmp_path / "src.permafrost"
        src.write_bytes(data)
        dst  = tmp_path / "dst.permafrost"
        sf   = str(tmp_path / "state.json")

        # State with wrong mtime → stale
        _save_state(sf, {
            "src_mtime":    0.0,
            "src_size":     len(data),
            "remote_uri":   str(dst),
            "bytes_written": 500_000,
        })

        result = LocalAdapter(str(tmp_path)).upload_resumable(
            str(src), str(dst), state_file=sf
        )

        assert Path(dst).read_bytes() == data
        assert result["resumed"] is False

    def test_missing_dst_on_resume_starts_fresh(self, tmp_path):
        data = os.urandom(512 * 1024)
        src  = tmp_path / "src.permafrost"
        src.write_bytes(data)
        dst  = tmp_path / "missing_dst.permafrost"
        sf   = str(tmp_path / "state.json")

        src_stat = src.stat()
        _save_state(sf, {
            "src_mtime":    src_stat.st_mtime,
            "src_size":     src_stat.st_size,
            "remote_uri":   str(dst),
            "bytes_written": 256_000,
        })
        # dst does NOT exist → must start fresh without error
        result = LocalAdapter(str(tmp_path)).upload_resumable(
            str(src), str(dst), state_file=sf
        )
        assert Path(dst).read_bytes() == data

    def test_chunked_write_multiple_chunks(self, tmp_path):
        data = os.urandom(3 * 1024 * 1024)
        src  = tmp_path / "src.permafrost"
        src.write_bytes(data)
        dst  = str(tmp_path / "dst.permafrost")

        LocalAdapter(str(tmp_path)).upload_resumable(
            str(src), dst, chunk_size=1024 * 1024
        )
        assert Path(dst).read_bytes() == data

    def test_upload_returns_correct_size(self, tmp_path):
        data = os.urandom(789_012)
        src  = tmp_path / "src.permafrost"
        src.write_bytes(data)
        dst  = str(tmp_path / "dst.permafrost")

        result = LocalAdapter(str(tmp_path)).upload_resumable(str(src), dst)
        assert result["size_bytes"] == 789_012

    def test_retry_on_write_error(self, tmp_path):
        data = os.urandom(256 * 1024)
        src  = tmp_path / "src.permafrost"
        src.write_bytes(data)
        dst  = str(tmp_path / "dst.permafrost")

        write_calls = [0]
        original_retry = __import__("permafrost.storage", fromlist=["_retry"])._retry

        def patched_retry(fn, max_retries=3, max_delay=60.0):
            # Replace inner fn with one that fails once on first chunk
            if write_calls[0] == 0:
                write_calls[0] += 1
                fail_once_calls = [0]
                def failing_fn():
                    fail_once_calls[0] += 1
                    if fail_once_calls[0] == 1:
                        raise OSError("simulated write error")
                    return fn()
                return original_retry(failing_fn, max_retries=3, max_delay=0.001)
            return fn()

        import permafrost.storage as storage_mod
        with patch.object(storage_mod, "_retry", side_effect=patched_retry):
            adapter = LocalAdapter(str(tmp_path))
            result  = adapter.upload_resumable(str(src), dst, max_retries=3)

        assert Path(dst).read_bytes() == data

    def test_ResumableUploadError_propagates(self, tmp_path):
        data = os.urandom(128 * 1024)
        src  = tmp_path / "src.permafrost"
        src.write_bytes(data)
        dst  = str(tmp_path / "dst.permafrost")

        import permafrost.storage as storage_mod

        def always_fail(fn, max_retries=3, max_delay=60.0):
            raise ResumableUploadError("max retries exceeded")

        with patch.object(storage_mod, "_retry", side_effect=always_fail):
            with pytest.raises(ResumableUploadError):
                LocalAdapter(str(tmp_path)).upload_resumable(str(src), dst)


# ── base-class fallback (non-LocalAdapter) ────────────────────────────────────

class TestBaseClassFallback:
    def test_base_upload_resumable_calls_upload(self, tmp_path):
        src = tmp_path / "src.permafrost"
        src.write_bytes(os.urandom(64 * 1024))
        dst = str(tmp_path / "dst.permafrost")

        adapter = LocalAdapter(str(tmp_path))
        # Call the base class method explicitly via super()
        from permafrost.storage import StorageAdapter
        result = StorageAdapter.upload_resumable(adapter, str(src), dst)
        assert Path(dst).exists()

"""
Tests for permafrost.ice — IceRecipe, parser, validator, watchers.
"""
import os
import time
import threading
import textwrap
from unittest.mock import MagicMock, patch, call
import pytest

from permafrost.ice import (
    IceRecipe,
    ValidationError,
    validate,
    parse_dict,
    parse_file,
    make_watcher,
    LocalWatcher,
    S3Watcher,
    _etag,
    DEFAULT_CHUNK_ROWS,
    DEFAULT_PRIORITY,
    DEFAULT_RETRY,
    ICE_VERSION,
    VALID_CODECS,
    VALID_PRIORITIES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

MINIMAL = {
    "name": "test-recipe",
    "source": "s3://raw/data/",
    "output": "s3://frozen/data.permafrost",
    "codec": "zstd",
}

FULL = {
    **MINIMAL,
    "quant": 2,
    "chunk_rows": 50_000,
    "partition_by": "date",
    "schedule": "0 2 * * *",
    "enabled": True,
    "timezone": "America/Sao_Paulo",
    "workers": 4,
    "priority": "high",
    "retry": 3,
    "timeout_minutes": 60,
    "description": "Daily climate archive",
    "owner": "data@example.com",
    "tags": ["climate", "daily"],
    "version": "2",
}


# ── IceRecipe dataclass ───────────────────────────────────────────────────────

class TestIceRecipe:
    def test_minimal_creation(self):
        r = parse_dict(MINIMAL)
        assert r.name == "test-recipe"
        assert r.source == "s3://raw/data/"
        assert r.output == "s3://frozen/data.permafrost"
        assert r.codec == "zstd"
        assert r.quant == 0
        assert r.chunk_rows == DEFAULT_CHUNK_ROWS
        assert r.priority == DEFAULT_PRIORITY
        assert r.retry == DEFAULT_RETRY
        assert r.version == ICE_VERSION
        assert r.enabled is True
        assert r.tags == []

    def test_full_creation(self):
        r = parse_dict(FULL)
        assert r.quant == 2
        assert r.chunk_rows == 50_000
        assert r.partition_by == "date"
        assert r.schedule == "0 2 * * *"
        assert r.timezone == "America/Sao_Paulo"
        assert r.workers == 4
        assert r.priority == "high"
        assert r.retry == 3
        assert r.timeout_minutes == 60
        assert r.description == "Daily climate archive"
        assert r.owner == "data@example.com"
        assert r.tags == ["climate", "daily"]
        assert r.version == "2"

    def test_to_dict_contains_required_fields(self):
        r = parse_dict(MINIMAL)
        d = r.to_dict()
        assert d["name"] == "test-recipe"
        assert d["codec"] == "zstd"
        assert "source_type" in d

    def test_to_dict_source_type_api_when_no_source_file(self):
        r = parse_dict(MINIMAL)
        assert r.to_dict()["source_type"] == "api"

    def test_to_dict_source_type_watcher_when_source_file_set(self):
        r = parse_dict(MINIMAL, source_file="/etc/recipes/test.ice")
        assert r.to_dict()["source_type"] == "watcher"

    def test_to_job_payload(self):
        r = parse_dict(FULL)
        p = r.to_job_payload()
        assert p["source_path"] == FULL["source"]
        assert p["output_path"] == FULL["output"]
        assert p["codec"] == "zstd"
        assert p["quant"] == 2
        assert p["chunk_rows"] == 50_000
        assert p["partition_by"] == "date"

    def test_to_job_payload_minimal_has_no_partition(self):
        r = parse_dict(MINIMAL)
        p = r.to_job_payload()
        assert p["partition_by"] is None

    def test_discovered_at_set_automatically(self):
        before = time.time()
        r = parse_dict(MINIMAL)
        after = time.time()
        assert before <= r.discovered_at <= after


# ── ValidationError ───────────────────────────────────────────────────────────

class TestValidationError:
    def test_str_format(self):
        e = ValidationError("codec", "invalid value")
        assert str(e) == "codec: invalid value"

    def test_str_with_field_name(self):
        e = ValidationError("name", '"name" is required')
        assert "name" in str(e)


# ── validate() ────────────────────────────────────────────────────────────────

class TestValidate:
    def test_valid_minimal_returns_no_errors(self):
        assert validate(MINIMAL) == []

    def test_valid_full_returns_no_errors(self):
        assert validate(FULL) == []

    def test_missing_name(self):
        errs = validate({**MINIMAL, "name": ""})
        assert any(e.field == "name" for e in errs)

    def test_missing_source(self):
        d = dict(MINIMAL); del d["source"]
        errs = validate(d)
        assert any(e.field == "source" for e in errs)

    def test_missing_output(self):
        d = dict(MINIMAL); del d["output"]
        errs = validate(d)
        assert any(e.field == "output" for e in errs)

    def test_missing_codec(self):
        d = dict(MINIMAL); del d["codec"]
        errs = validate(d)
        assert any(e.field == "codec" for e in errs)

    def test_invalid_codec(self):
        errs = validate({**MINIMAL, "codec": "brotli"})
        assert any(e.field == "codec" for e in errs)
        assert "brotli" in str(errs[0])

    def test_all_valid_codecs_pass(self):
        for codec in VALID_CODECS:
            assert validate({**MINIMAL, "codec": codec}) == []

    def test_negative_quant(self):
        errs = validate({**MINIMAL, "quant": -1})
        assert any(e.field == "quant" for e in errs)

    def test_zero_quant_ok(self):
        assert validate({**MINIMAL, "quant": 0}) == []

    def test_float_quant_ok(self):
        assert validate({**MINIMAL, "quant": 0.5}) == []

    def test_chunk_rows_too_small(self):
        errs = validate({**MINIMAL, "chunk_rows": 500})
        assert any(e.field == "chunk_rows" for e in errs)

    def test_chunk_rows_exactly_1000_ok(self):
        assert validate({**MINIMAL, "chunk_rows": 1000}) == []

    def test_chunk_rows_not_int(self):
        errs = validate({**MINIMAL, "chunk_rows": "big"})
        assert any(e.field == "chunk_rows" for e in errs)

    def test_invalid_priority(self):
        errs = validate({**MINIMAL, "priority": "urgent"})
        assert any(e.field == "priority" for e in errs)

    def test_valid_priorities_pass(self):
        for p in VALID_PRIORITIES:
            assert validate({**MINIMAL, "priority": p}) == []

    def test_multiple_errors_returned(self):
        errs = validate({"name": "", "source": "", "output": "", "codec": "bad"})
        assert len(errs) >= 3


# ── parse_dict() ──────────────────────────────────────────────────────────────

class TestParseDict:
    def test_minimal_ok(self):
        r = parse_dict(MINIMAL)
        assert isinstance(r, IceRecipe)

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid .ice recipe"):
            parse_dict({"name": "x", "source": "s", "output": "o", "codec": "FAKE"})

    def test_source_file_propagated(self):
        r = parse_dict(MINIMAL, source_file="/tmp/recipe.ice")
        assert r.source_file == "/tmp/recipe.ice"

    def test_tags_as_comma_string(self):
        r = parse_dict({**MINIMAL, "tags": "climate, daily, prod"})
        assert r.tags == ["climate", "daily", "prod"]

    def test_tags_as_list(self):
        r = parse_dict({**MINIMAL, "tags": ["a", "b"]})
        assert r.tags == ["a", "b"]

    def test_tags_default_empty(self):
        r = parse_dict(MINIMAL)
        assert r.tags == []

    def test_enabled_default_true(self):
        r = parse_dict(MINIMAL)
        assert r.enabled is True

    def test_enabled_false(self):
        r = parse_dict({**MINIMAL, "enabled": False})
        assert r.enabled is False

    def test_numeric_fields_cast(self):
        # parse_dict casts int/float values; validator accepts numeric types
        r = parse_dict({**MINIMAL, "quant": 2.0, "chunk_rows": 50000, "retry": 5})
        assert r.quant == 2
        assert r.chunk_rows == 50_000
        assert r.retry == 5


# ── parse_file() ──────────────────────────────────────────────────────────────

class TestParseFile:
    def test_valid_ice_file(self, tmp_path):
        f = tmp_path / "recipe.ice"
        f.write_text(textwrap.dedent("""\
            name: climate-daily
            source: s3://raw/climate/
            output: s3://frozen/climate.permafrost
            codec: zstd
            schedule: "0 2 * * *"
            owner: data@example.com
            tags: [climate, daily]
        """))
        r = parse_file(str(f))
        assert r.name == "climate-daily"
        assert r.schedule == "0 2 * * *"
        assert r.tags == ["climate", "daily"]
        assert r.source_file == str(f)

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_file(str(tmp_path / "missing.ice"))

    def test_invalid_yaml_type_raises(self, tmp_path):
        f = tmp_path / "bad.ice"
        f.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            parse_file(str(f))

    def test_validation_error_raises(self, tmp_path):
        f = tmp_path / "bad.ice"
        f.write_text("name: x\nsource: s\noutput: o\ncodec: NOTREAL\n")
        with pytest.raises(ValueError, match="Invalid .ice recipe"):
            parse_file(str(f))

    def test_all_optional_fields_parsed(self, tmp_path):
        f = tmp_path / "full.ice"
        f.write_text(textwrap.dedent("""\
            name: full-recipe
            source: /data/in/
            output: /data/out.permafrost
            codec: lzma2
            quant: 1
            chunk_rows: 10000
            partition_by: year
            schedule: "0 0 * * 0"
            enabled: false
            timezone: UTC
            workers: 2
            priority: low
            retry: 0
            timeout_minutes: 30
            description: full test
            owner: test@test.com
            tags: [a, b, c]
            version: "3"
        """))
        r = parse_file(str(f))
        assert r.enabled is False
        assert r.priority == "low"
        assert r.workers == 2
        assert r.timeout_minutes == 30
        assert len(r.tags) == 3


# ── _etag() ───────────────────────────────────────────────────────────────────

class TestEtag:
    def test_returns_string(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        e = _etag(str(f))
        assert isinstance(e, str)
        assert len(e) == 32  # md5 hex

    def test_same_file_same_etag(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        assert _etag(str(f)) == _etag(str(f))

    def test_changed_content_changes_etag(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        e1 = _etag(str(f))
        time.sleep(0.01)
        f.write_text("hello world")  # size changes
        e2 = _etag(str(f))
        assert e1 != e2


# ── LocalWatcher ─────────────────────────────────────────────────────────────

class TestLocalWatcher:
    def test_init_defaults(self, tmp_path):
        w = LocalWatcher(str(tmp_path))
        assert w.watch_path == str(tmp_path)
        assert w.poll_interval == 30.0
        assert w._known == {}

    def test_scan_empty_dir(self, tmp_path):
        added = []
        w = LocalWatcher(str(tmp_path), on_add=added.append)
        w._scan()
        assert added == []

    def test_scan_detects_new_ice_file(self, tmp_path):
        f = tmp_path / "r.ice"
        f.write_text("name: r\nsource: /s\noutput: /o.pf\ncodec: zstd\n")
        added = []
        w = LocalWatcher(str(tmp_path), on_add=added.append)
        w._scan()
        assert len(added) == 1
        assert added[0].name == "r"

    def test_scan_ignores_non_ice_files(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        (tmp_path / "data.yaml").write_text("key: val")
        added = []
        w = LocalWatcher(str(tmp_path), on_add=added.append)
        w._scan()
        assert added == []

    def test_scan_modified_file_triggers_on_add_again(self, tmp_path):
        f = tmp_path / "r.ice"
        f.write_text("name: r\nsource: /s\noutput: /o.pf\ncodec: zstd\n")
        added = []
        w = LocalWatcher(str(tmp_path), on_add=added.append)
        w._scan()
        assert len(added) == 1
        # Simulate a change (write bigger content to force size diff)
        f.write_text("name: r\nsource: /s/new/\noutput: /o.pf\ncodec: lzma2\n")
        w._scan()
        assert len(added) == 2

    def test_scan_unchanged_file_not_re_added(self, tmp_path):
        f = tmp_path / "r.ice"
        f.write_text("name: r\nsource: /s\noutput: /o.pf\ncodec: zstd\n")
        added = []
        w = LocalWatcher(str(tmp_path), on_add=added.append)
        w._scan()
        w._scan()
        assert len(added) == 1

    def test_scan_removed_file_triggers_on_remove(self, tmp_path):
        f = tmp_path / "r.ice"
        f.write_text("name: r\nsource: /s\noutput: /o.pf\ncodec: zstd\n")
        removed = []
        w = LocalWatcher(str(tmp_path), on_remove=removed.append)
        w._scan()
        f.unlink()
        w._scan()
        assert "r" in removed

    def test_scan_broken_file_skipped(self, tmp_path):
        f = tmp_path / "bad.ice"
        f.write_text("not: valid: yaml: [\n")  # broken
        added = []
        w = LocalWatcher(str(tmp_path), on_add=added.append)
        w._scan()  # must not raise
        assert added == []

    def test_scan_invalid_recipe_skipped(self, tmp_path):
        f = tmp_path / "bad.ice"
        f.write_text("name: x\nsource: s\noutput: o\ncodec: FAKECOD\n")
        added = []
        w = LocalWatcher(str(tmp_path), on_add=added.append)
        w._scan()
        assert added == []

    def test_scan_nonexistent_dir_is_noop(self, tmp_path):
        w = LocalWatcher(str(tmp_path / "doesnotexist"))
        w._scan()  # must not raise

    def test_start_stop(self, tmp_path):
        w = LocalWatcher(str(tmp_path), poll_interval=0.05)
        w.start()
        assert w._thread is not None
        assert w._thread.is_alive()
        w.stop()
        w._thread.join(timeout=2)
        assert not w._thread.is_alive()

    def test_multiple_files_all_detected(self, tmp_path):
        for i in range(3):
            (tmp_path / f"r{i}.ice").write_text(
                f"name: r{i}\nsource: /s{i}\noutput: /o{i}.pf\ncodec: zstd\n"
            )
        added = []
        w = LocalWatcher(str(tmp_path), on_add=added.append)
        w._scan()
        assert len(added) == 3
        assert {r.name for r in added} == {"r0", "r1", "r2"}


# ── S3Watcher ────────────────────────────────────────────────────────────────

def _make_s3_mock(objects):
    """Build a boto3 S3 client mock from a list of (key, etag, yaml_body) tuples."""
    client = MagicMock()
    paginator = MagicMock()
    client.get_paginator.return_value = paginator

    pages = [{"Contents": [
        {"Key": key, "ETag": f'"{etag}"'}
        for key, etag, _ in objects
    ]}]
    paginator.paginate.return_value = pages

    def get_object(Bucket, Key):
        for k, _, body in objects:
            if k == Key:
                resp_body = MagicMock()
                resp_body.read.return_value = body.encode()
                return {"Body": resp_body}
        raise KeyError(Key)

    client.get_object.side_effect = get_object
    return client


class TestS3Watcher:
    def _ice_yaml(self, name="s3-recipe", source="s3://raw/", output="s3://out.pf", codec="zstd"):
        return f"name: {name}\nsource: {source}\noutput: {output}\ncodec: {codec}\n"

    def test_scan_new_file_triggers_on_add(self):
        body = self._ice_yaml()
        mock_s3 = _make_s3_mock([("ice/r.ice", "abc123", body)])
        added = []
        w = S3Watcher("my-bucket", "ice/", on_add=added.append)
        w._s3 = mock_s3
        w._scan()
        assert len(added) == 1
        assert added[0].name == "s3-recipe"
        assert added[0].source_etag == "abc123"
        assert added[0].source_file == "s3://my-bucket/ice/r.ice"

    def test_scan_unchanged_etag_not_re_added(self):
        body = self._ice_yaml()
        mock_s3 = _make_s3_mock([("ice/r.ice", "abc123", body)])
        added = []
        w = S3Watcher("my-bucket", "ice/", on_add=added.append)
        w._s3 = mock_s3
        w._scan()
        w._scan()
        assert len(added) == 1

    def test_scan_changed_etag_triggers_on_add_again(self):
        body1 = self._ice_yaml(codec="zstd")
        body2 = self._ice_yaml(codec="lzma2")
        added = []
        w = S3Watcher("my-bucket", "ice/", on_add=added.append)

        mock1 = _make_s3_mock([("ice/r.ice", "etag1", body1)])
        w._s3 = mock1
        w._scan()
        assert len(added) == 1

        mock2 = _make_s3_mock([("ice/r.ice", "etag2", body2)])
        w._s3 = mock2
        w._scan()
        assert len(added) == 2

    def test_scan_removed_key_triggers_on_remove(self):
        body = self._ice_yaml()
        added, removed = [], []
        w = S3Watcher("my-bucket", "ice/", on_add=added.append, on_remove=removed.append)

        w._s3 = _make_s3_mock([("ice/r.ice", "etag1", body)])
        w._scan()
        w._s3 = _make_s3_mock([])   # key gone
        w._scan()
        assert "r" in removed

    def test_scan_ignores_non_ice_keys(self):
        # A .yaml file in the prefix should be skipped
        body = self._ice_yaml()
        mock_s3 = MagicMock()
        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{"Contents": [
            {"Key": "ice/notes.txt", "ETag": '"abc"'},
            {"Key": "ice/r.ice",     "ETag": '"def"'},
        ]}]
        resp_body = MagicMock()
        resp_body.read.return_value = body.encode()
        mock_s3.get_object.return_value = {"Body": resp_body}

        added = []
        w = S3Watcher("my-bucket", "ice/", on_add=added.append)
        w._s3 = mock_s3
        w._scan()
        assert len(added) == 1

    def test_scan_broken_yaml_skipped(self):
        mock_s3 = _make_s3_mock([("ice/bad.ice", "abc", "not: valid: yaml: [\n")])
        added = []
        w = S3Watcher("my-bucket", "ice/", on_add=added.append)
        w._s3 = mock_s3
        w._scan()  # must not raise
        assert added == []

    def test_scan_empty_prefix(self):
        mock_s3 = _make_s3_mock([])
        added = []
        w = S3Watcher("my-bucket", "ice/", on_add=added.append)
        w._s3 = mock_s3
        w._scan()
        assert added == []

    def test_start_stop(self):
        w = S3Watcher("my-bucket", "ice/", poll_interval=0.05)
        w._s3 = _make_s3_mock([])
        w.start()
        assert w._thread is not None
        assert w._thread.is_alive()
        w.stop()
        w._thread.join(timeout=2)
        assert not w._thread.is_alive()

    def test_client_lazy_init(self):
        import sys
        mock_boto3 = MagicMock()
        mock_s3_client = MagicMock()
        mock_boto3.client.return_value = mock_s3_client
        w = S3Watcher("my-bucket")
        assert w._s3 is None
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            c = w._client()
            assert c is mock_s3_client
            mock_boto3.client.assert_called_once_with("s3", endpoint_url=None)

    def test_client_with_endpoint_url(self):
        import sys
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        w = S3Watcher("my-bucket", endpoint_url="http://localhost:9000")
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            w._client()
            mock_boto3.client.assert_called_once_with("s3", endpoint_url="http://localhost:9000")


# ── make_watcher() ────────────────────────────────────────────────────────────

class TestMakeWatcher:
    def test_s3_path_returns_s3_watcher(self):
        w = make_watcher("s3://my-bucket/ice/")
        assert isinstance(w, S3Watcher)
        assert w.bucket == "my-bucket"
        assert w.prefix == "ice/"

    def test_s3_path_no_prefix_defaults_to_root(self):
        w = make_watcher("s3://my-bucket")
        assert isinstance(w, S3Watcher)
        assert w.bucket == "my-bucket"
        assert w.prefix == "/"

    def test_local_path_returns_local_watcher(self, tmp_path):
        w = make_watcher(str(tmp_path))
        assert isinstance(w, LocalWatcher)
        assert w.watch_path == str(tmp_path)

    def test_relative_path_returns_local_watcher(self):
        w = make_watcher("./recipes")
        assert isinstance(w, LocalWatcher)

    def test_callbacks_passed_through_s3(self):
        add_cb = lambda r: None
        rem_cb = lambda n: None
        w = make_watcher("s3://bucket/pfx/", on_add=add_cb, on_remove=rem_cb)
        assert w.on_add is add_cb
        assert w.on_remove is rem_cb

    def test_callbacks_passed_through_local(self, tmp_path):
        add_cb = lambda r: None
        rem_cb = lambda n: None
        w = make_watcher(str(tmp_path), on_add=add_cb, on_remove=rem_cb)
        assert w.on_add is add_cb
        assert w.on_remove is rem_cb

    def test_poll_interval_passed_to_s3(self):
        w = make_watcher("s3://bucket/", poll_interval=60.0)
        assert w.poll_interval == 60.0

    def test_poll_interval_passed_to_local(self, tmp_path):
        w = make_watcher(str(tmp_path), poll_interval=15.0)
        assert w.poll_interval == 15.0


# ── Public API (imported from permafrost top-level) ───────────────────────────

class TestPublicAPI:
    def test_load_ice(self, tmp_path):
        import permafrost as pf
        f = tmp_path / "r.ice"
        f.write_text("name: r\nsource: /s\noutput: /o.pf\ncodec: zstd\n")
        r = pf.load_ice(str(f))
        assert r.name == "r"

    def test_load_ice_dict(self):
        import permafrost as pf
        r = pf.load_ice_dict(MINIMAL)
        assert r.codec == "zstd"

    def test_validate_ice_no_errors(self):
        import permafrost as pf
        errors = pf.validate_ice(MINIMAL)
        assert errors == []

    def test_validate_ice_with_errors(self):
        import permafrost as pf
        errors = pf.validate_ice({"name": "", "source": "", "output": "", "codec": "bad"})
        assert len(errors) >= 1

    def test_ice_watcher_returns_local(self, tmp_path):
        import permafrost as pf
        w = pf.ice_watcher(str(tmp_path))
        assert isinstance(w, LocalWatcher)

    def test_ice_watcher_returns_s3(self):
        import permafrost as pf
        w = pf.ice_watcher("s3://bucket/ice/")
        assert isinstance(w, S3Watcher)

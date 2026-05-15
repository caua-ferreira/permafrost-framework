"""Tests for I1 — Kubernetes Operator (PermafrostJob CRD reconciler).

All tests run without a real Kubernetes cluster by mocking httpx
and the kopf patch object.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from permafrost.operator import (
    PHASE_COMPLETED,
    PHASE_FAILED,
    PHASE_PENDING,
    PHASE_RUNNING,
    cancel_job,
    get_job_status,
    master_health,
    on_create,
    on_delete,
    monitor,
    submit_job,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, body: dict):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


def _patch_obj():
    p = MagicMock()
    p.status = {}
    return p


# ── submit_job ────────────────────────────────────────────────────────────────

class TestSubmitJob:
    def test_basic_submit(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"job_id": "j-001"})
            result = submit_job("http://master:8700", {"sourcePath": "/data/file.csv"})
        assert result["job_id"] == "j-001"

    def test_url_trailing_slash_stripped(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"job_id": "j-002"})
            submit_job("http://master:8700/", {"sourcePath": "/x"})
        url_called = mock_post.call_args[0][0]
        assert url_called == "http://master:8700/jobs"

    def test_token_sent_as_bearer(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"job_id": "j"})
            submit_job("http://master:8700", {"sourcePath": "/x"}, token="tok123")
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer tok123"

    def test_no_token_no_auth_header(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"job_id": "j"})
            submit_job("http://master:8700", {"sourcePath": "/x"}, token=None)
        headers = mock_post.call_args[1]["headers"]
        assert "Authorization" not in headers

    def test_spec_fields_forwarded(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"job_id": "j"})
            submit_job("http://master:8700", {
                "sourcePath": "/x.csv",
                "outputPath": "/x.permafrost",
                "codec": "zstd",
                "quant": "high",
                "partitionBy": "year",
                "chunkRows": 5000,
            })
        payload = mock_post.call_args[1]["json"]
        assert payload["codec"] == "zstd"
        assert payload["quant"] == "high"
        assert payload["partition_by"] == "year"
        assert payload["chunk_rows"] == 5000

    def test_none_values_excluded_from_payload(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"job_id": "j"})
            submit_job("http://master:8700", {"sourcePath": "/x"})
        payload = mock_post.call_args[1]["json"]
        assert "output_path" not in payload
        assert "partition_by" not in payload

    def test_raises_on_http_error(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(401, {"detail": "Unauthorized"})
            with pytest.raises(httpx.HTTPStatusError):
                submit_job("http://master:8700", {"sourcePath": "/x"})

    def test_raises_on_network_error(self):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(httpx.ConnectError):
                submit_job("http://master:8700", {"sourcePath": "/x"})


# ── get_job_status ────────────────────────────────────────────────────────────

class TestGetJobStatus:
    def test_returns_status_dict(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(200, {"status": "running", "job_id": "j-1"})
            result = get_job_status("http://master:8700", "j-1")
        assert result["status"] == "running"

    def test_correct_url(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(200, {"status": "done"})
            get_job_status("http://master:8700", "job-xyz")
        assert "job-xyz" in mock_get.call_args[0][0]

    def test_raises_on_404(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(404, {"detail": "not found"})
            with pytest.raises(httpx.HTTPStatusError):
                get_job_status("http://master:8700", "ghost")

    def test_done_with_result(self):
        body = {"status": "done", "result": {"ratio": 8.5, "stored_mb": 1.2}}
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(200, body)
            result = get_job_status("http://master:8700", "j")
        assert result["result"]["ratio"] == 8.5


# ── cancel_job ────────────────────────────────────────────────────────────────

class TestCancelJob:
    def test_returns_true_on_200(self):
        with patch("httpx.delete") as mock_del:
            mock_del.return_value = _mock_response(200, {})
            assert cancel_job("http://master:8700", "j-1") is True

    def test_returns_true_on_204(self):
        with patch("httpx.delete") as mock_del:
            r = _mock_response(204, {})
            mock_del.return_value = r
            assert cancel_job("http://master:8700", "j-1") is True

    def test_returns_false_on_exception(self):
        with patch("httpx.delete", side_effect=httpx.ConnectError("refused")):
            assert cancel_job("http://master:8700", "j-1") is False

    def test_returns_false_on_500(self):
        with patch("httpx.delete") as mock_del:
            mock_del.return_value = _mock_response(500, {})
            assert cancel_job("http://master:8700", "j-1") is False


# ── master_health ─────────────────────────────────────────────────────────────

class TestMasterHealth:
    def test_true_on_200(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(200, {"status": "ok"})
            assert master_health("http://master:8700") is True

    def test_false_on_503(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(503, {})
            assert master_health("http://master:8700") is False

    def test_false_on_connection_error(self):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            assert master_health("http://master:8700") is False


# ── on_create handler ─────────────────────────────────────────────────────────

class TestOnCreate:
    def test_sets_running_on_success(self):
        patch_obj = _patch_obj()
        spec = {"sourcePath": "/data/x.csv", "masterUrl": "http://m:8700"}
        with patch("permafrost.operator.submit_job", return_value={"job_id": "j-42"}):
            on_create(spec=spec, name="job1", namespace="default", patch=patch_obj)
        assert patch_obj.status["phase"] == PHASE_RUNNING
        assert patch_obj.status["jobId"] == "j-42"

    def test_sets_failed_on_submit_error(self):
        import kopf
        patch_obj = _patch_obj()
        spec = {"sourcePath": "/data/x.csv", "masterUrl": "http://m:8700"}
        with patch("permafrost.operator.submit_job",
                   side_effect=httpx.ConnectError("refused")):
            with pytest.raises(kopf.PermanentError):
                on_create(spec=spec, name="job1", namespace="default", patch=patch_obj)
        assert patch_obj.status["phase"] == PHASE_FAILED

    def test_uses_master_url_from_spec(self):
        patch_obj = _patch_obj()
        spec = {"sourcePath": "/x", "masterUrl": "http://custom:9000"}
        with patch("permafrost.operator.submit_job", return_value={"job_id": "j"}) as m:
            on_create(spec=spec, name="n", namespace="ns", patch=patch_obj)
        assert m.call_args[0][0] == "http://custom:9000"

    def test_start_time_set(self):
        patch_obj = _patch_obj()
        spec = {"sourcePath": "/x", "masterUrl": "http://m:8700"}
        with patch("permafrost.operator.submit_job", return_value={"job_id": "j"}):
            on_create(spec=spec, name="n", namespace="ns", patch=patch_obj)
        assert "startTime" in patch_obj.status

    def test_token_forwarded(self):
        patch_obj = _patch_obj()
        spec = {"sourcePath": "/x", "masterUrl": "http://m:8700", "token": "my-jwt"}
        with patch("permafrost.operator.submit_job", return_value={"job_id": "j"}) as m:
            on_create(spec=spec, name="n", namespace="ns", patch=patch_obj)
        assert m.call_args[0][2] == "my-jwt"


# ── monitor handler ───────────────────────────────────────────────────────────

class TestMonitor:
    def _run(self, spec, status, job_status_body=None, error=None):
        patch_obj = _patch_obj()
        if error:
            with patch("permafrost.operator.get_job_status", side_effect=error):
                monitor(spec=spec, name="n", namespace="ns",
                        status=status, patch=patch_obj)
        else:
            with patch("permafrost.operator.get_job_status",
                       return_value=job_status_body):
                monitor(spec=spec, name="n", namespace="ns",
                        status=status, patch=patch_obj)
        return patch_obj

    def test_sets_completed_on_done(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_RUNNING, "jobId": "j-1"}
        body = {"status": "done", "result": {"ratio": 9.0, "stored_mb": 0.5}}
        p = self._run(spec, status, body)
        assert p.status["phase"] == PHASE_COMPLETED
        assert p.status["ratio"] == 9.0

    def test_sets_failed_on_failed(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_RUNNING, "jobId": "j-1"}
        body = {"status": "failed", "error": "OOM"}
        p = self._run(spec, status, body)
        assert p.status["phase"] == PHASE_FAILED
        assert "OOM" in p.status["message"]

    def test_no_update_if_already_completed(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_COMPLETED, "jobId": "j-1"}
        p = _patch_obj()
        with patch("permafrost.operator.get_job_status") as mock_get:
            monitor(spec=spec, name="n", namespace="ns",
                    status=status, patch=p)
        mock_get.assert_not_called()

    def test_no_update_if_no_job_id(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_RUNNING, "jobId": ""}
        p = _patch_obj()
        with patch("permafrost.operator.get_job_status") as mock_get:
            monitor(spec=spec, name="n", namespace="ns",
                    status=status, patch=p)
        mock_get.assert_not_called()

    def test_sets_failed_on_404(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_RUNNING, "jobId": "j-gone"}
        resp_404 = _mock_response(404, {})
        err = httpx.HTTPStatusError("not found", request=MagicMock(), response=resp_404)
        p = self._run(spec, status, error=err)
        assert p.status["phase"] == PHASE_FAILED

    def test_completion_time_set(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_RUNNING, "jobId": "j-1"}
        body = {"status": "done", "result": {}}
        p = self._run(spec, status, body)
        assert "completionTime" in p.status


# ── on_delete handler ─────────────────────────────────────────────────────────

class TestOnDelete:
    def test_cancels_running_job(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_RUNNING, "jobId": "j-99"}
        with patch("permafrost.operator.cancel_job") as mock_cancel:
            on_delete(spec=spec, name="n", namespace="ns", status=status)
        mock_cancel.assert_called_once_with("http://m:8700", "j-99", None)

    def test_skips_cancel_if_not_running(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_COMPLETED, "jobId": "j-99"}
        with patch("permafrost.operator.cancel_job") as mock_cancel:
            on_delete(spec=spec, name="n", namespace="ns", status=status)
        mock_cancel.assert_not_called()

    def test_skips_cancel_if_no_job_id(self):
        spec = {"masterUrl": "http://m:8700"}
        status = {"phase": PHASE_RUNNING, "jobId": ""}
        with patch("permafrost.operator.cancel_job") as mock_cancel:
            on_delete(spec=spec, name="n", namespace="ns", status=status)
        mock_cancel.assert_not_called()

    def test_forwards_token(self):
        spec = {"masterUrl": "http://m:8700", "token": "tkn"}
        status = {"phase": PHASE_RUNNING, "jobId": "j-1"}
        with patch("permafrost.operator.cancel_job") as mock_cancel:
            on_delete(spec=spec, name="n", namespace="ns", status=status)
        assert mock_cancel.call_args[0][2] == "tkn"


# ── CRD YAML sanity ───────────────────────────────────────────────────────────

class TestCRDYaml:
    def test_crd_file_exists(self):
        import os
        crd_path = os.path.join(
            os.path.dirname(__file__),
            "..", "charts", "permafrost", "crds", "permafrostjob.yaml"
        )
        assert os.path.exists(crd_path)

    def test_crd_is_valid_yaml(self):
        import os
        import yaml
        crd_path = os.path.join(
            os.path.dirname(__file__),
            "..", "charts", "permafrost", "crds", "permafrostjob.yaml"
        )
        with open(crd_path) as f:
            doc = yaml.safe_load(f)
        assert doc["kind"] == "CustomResourceDefinition"
        assert doc["spec"]["names"]["kind"] == "PermafrostJob"

    def test_crd_required_field_source_path(self):
        import os
        import yaml
        crd_path = os.path.join(
            os.path.dirname(__file__),
            "..", "charts", "permafrost", "crds", "permafrostjob.yaml"
        )
        with open(crd_path) as f:
            doc = yaml.safe_load(f)
        versions = doc["spec"]["versions"]
        v1 = next(v for v in versions if v["name"] == "v1alpha1")
        required = v1["schema"]["openAPIV3Schema"]["properties"]["spec"]["required"]
        assert "sourcePath" in required

    def test_crd_phases_enum(self):
        import os
        import yaml
        crd_path = os.path.join(
            os.path.dirname(__file__),
            "..", "charts", "permafrost", "crds", "permafrostjob.yaml"
        )
        with open(crd_path) as f:
            doc = yaml.safe_load(f)
        versions = doc["spec"]["versions"]
        v1 = next(v for v in versions if v["name"] == "v1alpha1")
        status_props = v1["schema"]["openAPIV3Schema"]["properties"]["status"]["properties"]
        phase_enum = status_props["phase"]["enum"]
        assert set(phase_enum) == {"Pending", "Running", "Completed", "Failed"}

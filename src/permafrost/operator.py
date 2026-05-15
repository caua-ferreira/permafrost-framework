"""
Permafrost Kubernetes Operator — I1 feature (v0.8)

Watches PermafrostJob CRDs and reconciles them against the
PermafrostMaster REST API.  Requires kopf + kubernetes client.

Entry point (kopf CLI):
    kopf run -A src/permafrost/operator.py

Or via the Docker image (Dockerfile.operator):
    docker run caua-ferreira/permafrost-operator
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Phases ────────────────────────────────────────────────────────────────────

PHASE_PENDING   = "Pending"
PHASE_RUNNING   = "Running"
PHASE_COMPLETED = "Completed"
PHASE_FAILED    = "Failed"

# ── Master HTTP helpers ───────────────────────────────────────────────────────

def _headers(token: Optional[str]) -> dict:
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def submit_job(master_url: str, spec: dict, token: Optional[str] = None) -> dict:
    """Submits a PermafrostJob spec to the master REST API.

    Args:
        master_url: Base URL of PermafrostMaster, e.g. ``http://master:8700``.
        spec: CRD spec dict with at minimum ``sourcePath``.
        token: Optional JWT token for RBAC-enabled clusters.

    Returns:
        Response dict from master containing ``job_id``.

    Raises:
        httpx.HTTPStatusError: If the master returns an error response.
        httpx.RequestError: If the master is unreachable.
    """
    payload = {
        "source_path":  spec.get("sourcePath", ""),
        "output_path":  spec.get("outputPath"),
        "codec":        spec.get("codec", "lzma2"),
        "quant":        spec.get("quant", "none"),
        "partition_by": spec.get("partitionBy"),
        "chunk_rows":   spec.get("chunkRows", 10_000),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    resp = httpx.post(
        f"{master_url.rstrip('/')}/jobs",
        json=payload,
        headers=_headers(token),
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def get_job_status(master_url: str, job_id: str, token: Optional[str] = None) -> dict:
    """Polls the master for the current status of a job.

    Args:
        master_url: Base URL of PermafrostMaster.
        job_id: Job identifier returned by :func:`submit_job`.
        token: Optional JWT token.

    Returns:
        Status dict with at minimum ``status`` (``pending`` | ``running`` |
        ``done`` | ``failed``) and optionally ``result`` on completion.

    Raises:
        httpx.HTTPStatusError: On HTTP errors (404 = job not found).
    """
    resp = httpx.get(
        f"{master_url.rstrip('/')}/jobs/{job_id}",
        headers=_headers(token),
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def cancel_job(master_url: str, job_id: str, token: Optional[str] = None) -> bool:
    """Cancels a running job on the master.

    Args:
        master_url: Base URL of PermafrostMaster.
        job_id: Job identifier.
        token: Optional JWT token.

    Returns:
        ``True`` if the cancellation request was accepted.
    """
    try:
        resp = httpx.delete(
            f"{master_url.rstrip('/')}/jobs/{job_id}",
            headers=_headers(token),
            timeout=10.0,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def master_health(master_url: str) -> bool:
    """Returns True if the master /health endpoint responds OK."""
    try:
        resp = httpx.get(f"{master_url.rstrip('/')}/health", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


# ── kopf handlers ─────────────────────────────────────────────────────────────
# These are registered only when kopf is available (operator runtime).

try:
    import kopf

    @kopf.on.create("permafrost.io", "v1alpha1", "permafrostjobs")
    def on_create(spec: dict, name: str, namespace: str, patch: Any, **kwargs: Any) -> dict:
        """Handles new PermafrostJob — submits it to the master.

        Sets initial status to ``Pending`` and stores the ``jobId``
        returned by the master so the monitor daemon can poll it.
        """
        master_url = spec.get("masterUrl", "http://permafrost-master:8700")
        token      = spec.get("token")

        logger.info("PermafrostJob %s/%s created — submitting to %s", namespace, name, master_url)

        patch.status["phase"]     = PHASE_PENDING
        patch.status["message"]   = "Submitting job to master"
        patch.status["startTime"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        try:
            result = submit_job(master_url, spec, token)
            job_id = result.get("job_id", "")
            patch.status["phase"]   = PHASE_RUNNING
            patch.status["jobId"]   = job_id
            patch.status["message"] = f"Job submitted: {job_id}"
            logger.info("PermafrostJob %s/%s → job_id=%s", namespace, name, job_id)
        except Exception as exc:
            patch.status["phase"]   = PHASE_FAILED
            patch.status["message"] = str(exc)
            logger.error("PermafrostJob %s/%s failed to submit: %s", namespace, name, exc)
            raise kopf.PermanentError(f"Submit failed: {exc}") from exc

        return {"jobId": job_id}

    @kopf.timer(
        "permafrost.io", "v1alpha1", "permafrostjobs",
        initial_delay=5,
        interval=15,
    )
    def monitor(
        spec: dict, name: str, namespace: str, status: dict,
        patch: Any, **kwargs: Any,
    ) -> None:
        """Timer that polls the master every 15 s and updates CRD status.

        Skips immediately if the job is already in a terminal phase
        (Completed or Failed).
        """
        phase = status.get("phase", PHASE_PENDING)
        if phase in (PHASE_COMPLETED, PHASE_FAILED):
            return

        job_id     = status.get("jobId", "")
        master_url = spec.get("masterUrl", "http://permafrost-master:8700")
        token      = spec.get("token")

        if not job_id:
            return

        try:
            info = get_job_status(master_url, job_id, token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                patch.status["phase"]   = PHASE_FAILED
                patch.status["message"] = f"Job {job_id} not found on master"
            return
        except Exception as exc:
            logger.warning("Could not poll job %s: %s", job_id, exc)
            return

        master_status = info.get("status", "")

        if master_status == "done":
            result = info.get("result", {})
            patch.status["phase"]          = PHASE_COMPLETED
            patch.status["message"]        = "Job completed successfully"
            patch.status["completionTime"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if result:
                patch.status["ratio"]    = result.get("ratio", 0.0)
                patch.status["storedMb"] = result.get("stored_mb", 0.0)
            logger.info("PermafrostJob %s/%s completed (ratio=%.2f×)",
                        namespace, name, patch.status.get("ratio", 0))

        elif master_status == "failed":
            patch.status["phase"]          = PHASE_FAILED
            patch.status["message"]        = info.get("error", "Job failed on worker")
            patch.status["completionTime"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            logger.error("PermafrostJob %s/%s failed: %s", namespace, name, patch.status["message"])

        elif master_status in ("pending", "running"):
            patch.status["phase"]   = PHASE_RUNNING
            patch.status["message"] = f"Master status: {master_status}"

    @kopf.on.delete("permafrost.io", "v1alpha1", "permafrostjobs")
    def on_delete(spec: dict, name: str, namespace: str, status: dict, **kwargs: Any) -> None:
        """Cancels the job on the master when the CRD is deleted."""
        job_id     = status.get("jobId", "")
        master_url = spec.get("masterUrl", "http://permafrost-master:8700")
        token      = spec.get("token")

        if job_id and status.get("phase") == PHASE_RUNNING:
            logger.info("Cancelling job %s for deleted PermafrostJob %s/%s", job_id, namespace, name)
            cancel_job(master_url, job_id, token)

except ImportError:
    logger.debug("kopf not available — operator handlers not registered")

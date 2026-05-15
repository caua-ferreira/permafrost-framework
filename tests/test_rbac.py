"""Tests for I4 — RBAC Básico no Cluster."""
import time
import pytest
from fastapi.testclient import TestClient

import permafrost as pf
from permafrost.rbac import (
    AuthError,
    ClusterUser,
    RBACManager,
    generate_token,
    validate_token,
)
from permafrost.cluster import PermafrostMaster


SECRET = "super-secret-key-for-tests"


# ── generate_token / validate_token ──────────────────────────────────────────

class TestJWT:
    def test_roundtrip_returns_claims(self):
        token = generate_token("alice", can_freeze=True, can_thaw=True,
                               namespace="prod", secret_key=SECRET)
        claims = validate_token(token, SECRET)
        assert claims["sub"] == "alice"
        assert claims["can_freeze"] is True
        assert claims["can_thaw"] is True
        assert claims["namespace"] == "prod"

    def test_bearer_prefix_stripped(self):
        token = generate_token("bob", False, True, "dev", SECRET)
        claims = validate_token(f"Bearer {token}", SECRET)
        assert claims["sub"] == "bob"

    def test_wrong_secret_raises(self):
        token = generate_token("eve", True, True, "default", SECRET)
        with pytest.raises(AuthError, match="Assinatura"):
            validate_token(token, "wrong-secret")

    def test_tampered_payload_raises(self):
        token = generate_token("alice", True, True, "prod", SECRET)
        parts = token.split(".")
        # flip a bit in the payload
        tampered = parts[0] + "." + parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B") + "." + parts[2]
        with pytest.raises(AuthError):
            validate_token(tampered, SECRET)

    def test_empty_token_raises(self):
        with pytest.raises(AuthError, match="ausente"):
            validate_token("", SECRET)

    def test_malformed_token_raises(self):
        with pytest.raises(AuthError, match="malformado"):
            validate_token("not.a.valid.jwt.extra", SECRET)

    def test_expired_token_raises(self):
        token = generate_token("alice", True, True, "prod", SECRET, expires_in=1)
        time.sleep(1.05)
        with pytest.raises(AuthError, match="expirado"):
            validate_token(token, SECRET)

    def test_no_expiry_never_expires(self):
        token = generate_token("alice", True, True, "prod", SECRET, expires_in=0)
        claims = validate_token(token, SECRET)
        assert "exp" not in claims

    def test_iat_is_recent(self):
        before = int(time.time())
        token = generate_token("alice", True, True, "prod", SECRET)
        after = int(time.time())
        claims = validate_token(token, SECRET)
        assert before <= claims["iat"] <= after


# ── RBACManager ───────────────────────────────────────────────────────────────

class TestRBACManager:
    def test_add_user_returns_valid_token(self):
        rbac = RBACManager(SECRET)
        token = rbac.add_user("alice", can_freeze=True, can_thaw=True)
        claims = validate_token(token, SECRET)
        assert claims["sub"] == "alice"

    def test_empty_secret_raises(self):
        with pytest.raises(ValueError):
            RBACManager("")

    def test_validate_freeze_permission(self):
        rbac = RBACManager(SECRET)
        token = rbac.add_user("alice", can_freeze=True)
        claims = rbac.validate(token, require_freeze=True)
        assert claims["sub"] == "alice"

    def test_validate_no_freeze_raises(self):
        rbac = RBACManager(SECRET)
        token = rbac.add_user("readonly", can_freeze=False, can_thaw=True)
        with pytest.raises(AuthError, match="can_freeze"):
            rbac.validate(token, require_freeze=True)

    def test_validate_thaw_via_freeze_perm(self):
        rbac = RBACManager(SECRET)
        # can_freeze implies can also read (thaw)
        token = rbac.add_user("superuser", can_freeze=True, can_thaw=False)
        claims = rbac.validate(token, require_thaw=True)
        assert claims is not None

    def test_validate_no_thaw_or_freeze_raises(self):
        rbac = RBACManager(SECRET)
        token = rbac.add_user("nobody", can_freeze=False, can_thaw=False)
        with pytest.raises(AuthError, match="can_thaw"):
            rbac.validate(token, require_thaw=True)

    def test_list_users(self):
        rbac = RBACManager(SECRET)
        rbac.add_user("alice", can_freeze=True)
        rbac.add_user("bob",   can_thaw=True)
        users = rbac.list_users()
        names = [u["username"] for u in users]
        assert "alice" in names and "bob" in names

    def test_remove_user_returns_true(self):
        rbac = RBACManager(SECRET)
        rbac.add_user("temp")
        assert rbac.remove_user("temp") is True

    def test_remove_nonexistent_returns_false(self):
        rbac = RBACManager(SECRET)
        assert rbac.remove_user("ghost") is False

    def test_verify_admin_key_correct(self):
        rbac = RBACManager(SECRET)
        assert rbac.verify_admin_key(SECRET) is True

    def test_verify_admin_key_wrong(self):
        rbac = RBACManager(SECRET)
        assert rbac.verify_admin_key("wrong") is False

    def test_verify_admin_key_empty(self):
        rbac = RBACManager(SECRET)
        assert rbac.verify_admin_key("") is False

    def test_namespace_in_token(self):
        rbac = RBACManager(SECRET)
        token = rbac.add_user("alice", namespace="prod")
        claims = validate_token(token, SECRET)
        assert claims["namespace"] == "prod"


# ── Master endpoints sem RBAC (backward compat) ───────────────────────────────

class TestMasterNoRBAC:
    @pytest.fixture
    def client(self):
        master = PermafrostMaster()
        return TestClient(master.app)

    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["rbac_enabled"] is False

    def test_jobs_no_token_allowed(self, client):
        resp = client.post("/jobs", json={"source_path": "x.csv"})
        assert resp.status_code == 200

    def test_list_jobs_no_token_allowed(self, client):
        resp = client.get("/jobs")
        assert resp.status_code == 200

    def test_list_workers_no_token_allowed(self, client):
        resp = client.get("/workers")
        assert resp.status_code == 200

    def test_admin_endpoint_400_without_rbac(self, client):
        resp = client.post("/admin/users",
                           json={"username": "alice"},
                           headers={"X-Admin-Key": SECRET})
        assert resp.status_code == 400


# ── Master endpoints com RBAC ─────────────────────────────────────────────────

class TestMasterWithRBAC:
    @pytest.fixture
    def master(self):
        return PermafrostMaster(secret_key=SECRET)

    @pytest.fixture
    def client(self, master):
        return TestClient(master.app)

    @pytest.fixture
    def freeze_token(self, master):
        return master._rbac.add_user("freezer", can_freeze=True, can_thaw=True)

    @pytest.fixture
    def thaw_token(self, master):
        return master._rbac.add_user("reader", can_freeze=False, can_thaw=True)

    def test_health_rbac_enabled(self, client):
        assert client.get("/health").json()["rbac_enabled"] is True

    def test_submit_job_no_token_returns_401(self, client):
        resp = client.post("/jobs", json={"source_path": "x.csv"})
        assert resp.status_code == 401

    def test_submit_job_valid_freeze_token(self, client, freeze_token):
        resp = client.post("/jobs", json={"source_path": "x.csv"},
                           headers={"Authorization": f"Bearer {freeze_token}"})
        assert resp.status_code == 200

    def test_submit_job_thaw_only_token_returns_403(self, client, thaw_token):
        resp = client.post("/jobs", json={"source_path": "x.csv"},
                           headers={"Authorization": f"Bearer {thaw_token}"})
        assert resp.status_code == 403

    def test_list_jobs_no_token_returns_401(self, client):
        assert client.get("/jobs").status_code == 401

    def test_list_jobs_thaw_token_allowed(self, client, thaw_token):
        assert client.get("/jobs",
                          headers={"Authorization": f"Bearer {thaw_token}"}).status_code == 200

    def test_get_job_nonexistent_returns_404(self, client, thaw_token):
        resp = client.get("/jobs/nonexistent",
                          headers={"Authorization": f"Bearer {thaw_token}"})
        assert resp.status_code == 404

    def test_cancel_job_thaw_only_returns_403(self, client, thaw_token):
        resp = client.delete("/jobs/somejob",
                             headers={"Authorization": f"Bearer {thaw_token}"})
        assert resp.status_code == 403

    def test_list_workers_no_token_returns_401(self, client):
        assert client.get("/workers").status_code == 401

    def test_list_workers_thaw_token_allowed(self, client, thaw_token):
        assert client.get("/workers",
                          headers={"Authorization": f"Bearer {thaw_token}"}).status_code == 200

    def test_worker_register_no_auth_required(self, client):
        resp = client.post("/workers/register",
                           json={"worker_id": "w1", "host": "127.0.0.1", "port": 9000})
        assert resp.status_code == 200

    def test_admin_create_user_wrong_key_returns_403(self, client):
        resp = client.post("/admin/users",
                           json={"username": "alice"},
                           headers={"X-Admin-Key": "wrong"})
        assert resp.status_code == 403

    def test_admin_create_user_correct_key(self, client):
        resp = client.post("/admin/users",
                           json={"username": "newuser", "can_freeze": True,
                                 "can_thaw": True, "namespace": "prod"},
                           headers={"X-Admin-Key": SECRET})
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "newuser"
        assert "token" in data
        claims = validate_token(data["token"], SECRET)
        assert claims["namespace"] == "prod"

    def test_admin_list_users(self, client):
        client.post("/admin/users",
                    json={"username": "u1", "can_freeze": True},
                    headers={"X-Admin-Key": SECRET})
        resp = client.get("/admin/users", headers={"X-Admin-Key": SECRET})
        assert resp.status_code == 200
        names = [u["username"] for u in resp.json()]
        assert "u1" in names

    def test_admin_delete_user(self, client):
        client.post("/admin/users",
                    json={"username": "todelete"},
                    headers={"X-Admin-Key": SECRET})
        resp = client.delete("/admin/users/todelete",
                             headers={"X-Admin-Key": SECRET})
        assert resp.status_code == 200
        assert resp.json()["existed"] is True

    def test_admin_delete_nonexistent_user(self, client):
        resp = client.delete("/admin/users/ghost",
                             headers={"X-Admin-Key": SECRET})
        assert resp.status_code == 200
        assert resp.json()["existed"] is False

    def test_task_callbacks_no_auth_required(self, client, freeze_token):
        # Submit a job first
        job_resp = client.post("/jobs", json={"source_path": "x.csv"},
                               headers={"Authorization": f"Bearer {freeze_token}"})
        job_id = job_resp.json()["job_id"]
        # Worker callbacks must work without auth (internal)
        resp = client.post(f"/jobs/{job_id}/tasks/t0/done",
                           json={"result": {"rows": 100}})
        assert resp.status_code == 200

    def test_wrong_token_returns_403(self, client):
        bad_token = generate_token("hacker", True, True, "default", "other-secret")
        resp = client.post("/jobs", json={"source_path": "x.csv"},
                           headers={"Authorization": f"Bearer {bad_token}"})
        assert resp.status_code == 403


# ── Exports ───────────────────────────────────────────────────────────────────

class TestExports:
    def test_AuthError_exported(self):
        assert pf.AuthError is AuthError

    def test_RBACManager_exported(self):
        assert pf.RBACManager is RBACManager

    def test_generate_token_exported(self):
        assert callable(pf.generate_token)

    def test_validate_token_exported(self):
        assert callable(pf.validate_token)

    def test_ClusterUser_exported(self):
        assert pf.ClusterUser is ClusterUser

"""
Tests for the /recipes REST endpoints added to PermafrostMaster in v1.4.0,
and for PermafrostMaster's watcher integration (_on_ice_add / _on_ice_remove).
"""
import pytest
from fastapi.testclient import TestClient

from permafrost.cluster import PermafrostMaster
from permafrost.ice import IceRecipe, parse_dict


MINIMAL = {
    "name": "climate",
    "source": "s3://raw/climate/",
    "output": "s3://frozen/climate.permafrost",
    "codec": "zstd",
}

FULL = {
    **MINIMAL,
    "name": "full-recipe",
    "quant": 1,
    "chunk_rows": 50_000,
    "partition_by": "date",
    "schedule": "0 2 * * *",
    "owner": "data@example.com",
    "tags": ["climate", "daily"],
    "priority": "high",
}


@pytest.fixture
def client():
    master = PermafrostMaster(host="127.0.0.1", port=18700)
    return TestClient(master.app)


@pytest.fixture
def authed_client():
    master = PermafrostMaster(host="127.0.0.1", port=18701, secret_key="testsecret")
    return TestClient(master.app)


# ── GET /recipes ───────────────────────────────────────────────────────────────

class TestListRecipes:
    def test_empty_returns_list(self, client):
        r = client.get("/recipes")
        assert r.status_code == 200
        assert r.json() == []

    def test_after_create_returns_recipe(self, client):
        client.post("/recipes", json=MINIMAL)
        r = client.get("/recipes")
        names = [x["name"] for x in r.json()]
        assert "climate" in names


# ── POST /recipes ─────────────────────────────────────────────────────────────

class TestCreateRecipe:
    def test_create_minimal(self, client):
        r = client.post("/recipes", json=MINIMAL)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "climate"
        assert data["codec"] == "zstd"
        assert data["source_type"] == "api"

    def test_create_full(self, client):
        r = client.post("/recipes", json=FULL)
        assert r.status_code == 200
        d = r.json()
        assert d["priority"] == "high"
        assert d["partition_by"] == "date"

    def test_create_invalid_codec_returns_422(self, client):
        r = client.post("/recipes", json={**MINIMAL, "name": "bad", "codec": "FAKE"})
        assert r.status_code == 422

    def test_create_missing_required_field_returns_422(self, client):
        r = client.post("/recipes", json={"name": "x", "source": "s", "output": "o"})
        assert r.status_code == 422

    def test_create_duplicate_returns_409(self, client):
        payload = {**MINIMAL, "name": "dup-test"}
        client.post("/recipes", json=payload)
        r = client.post("/recipes", json=payload)
        assert r.status_code == 409


# ── GET /recipes/{name} ───────────────────────────────────────────────────────

class TestGetRecipe:
    def test_get_existing(self, client):
        client.post("/recipes", json={**MINIMAL, "name": "get-me"})
        r = client.get("/recipes/get-me")
        assert r.status_code == 200
        assert r.json()["name"] == "get-me"

    def test_get_missing_returns_404(self, client):
        r = client.get("/recipes/does-not-exist")
        assert r.status_code == 404


# ── PUT /recipes/{name} ───────────────────────────────────────────────────────

class TestUpdateRecipe:
    def test_partial_update_changes_field(self, client):
        client.post("/recipes", json={**MINIMAL, "name": "upd-me"})
        r = client.put("/recipes/upd-me", json={"codec": "lzma2"})
        assert r.status_code == 200
        assert r.json()["codec"] == "lzma2"

    def test_update_preserves_other_fields(self, client):
        client.post("/recipes", json={**MINIMAL, "name": "upd-keep", "owner": "owner@x.com"})
        r = client.put("/recipes/upd-keep", json={"quant": 1})
        assert r.json()["owner"] == "owner@x.com"
        assert r.json()["quant"] == 1

    def test_update_missing_recipe_returns_404(self, client):
        r = client.put("/recipes/ghost", json={"codec": "zstd"})
        assert r.status_code == 404

    def test_update_invalid_value_returns_422(self, client):
        client.post("/recipes", json={**MINIMAL, "name": "upd-bad"})
        r = client.put("/recipes/upd-bad", json={"codec": "INVALID"})
        assert r.status_code == 422


# ── DELETE /recipes/{name} ────────────────────────────────────────────────────

class TestDeleteRecipe:
    def test_delete_existing(self, client):
        client.post("/recipes", json={**MINIMAL, "name": "del-me"})
        r = client.delete("/recipes/del-me")
        assert r.status_code == 200
        assert r.json()["deleted"] == "del-me"
        # confirm gone
        assert client.get("/recipes/del-me").status_code == 404

    def test_delete_missing_returns_404(self, client):
        r = client.delete("/recipes/no-such-recipe")
        assert r.status_code == 404


# ── POST /recipes/{name}/run ──────────────────────────────────────────────────

class TestRunRecipe:
    def test_run_returns_job_id(self, client):
        client.post("/recipes", json={**MINIMAL, "name": "run-me"})
        r = client.post("/recipes/run-me/run")
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data["recipe"] == "run-me"

    def test_run_missing_recipe_returns_404(self, client):
        r = client.post("/recipes/ghost/run")
        assert r.status_code == 404

    def test_run_disabled_recipe_returns_400(self, client):
        client.post("/recipes", json={**MINIMAL, "name": "disabled-r", "enabled": False})
        r = client.post("/recipes/disabled-r/run")
        assert r.status_code == 400

    def test_run_creates_job_visible_via_get_jobs(self, client):
        client.post("/recipes", json={**MINIMAL, "name": "run-job"})
        run_resp = client.post("/recipes/run-job/run")
        job_id = run_resp.json()["job_id"]
        jobs = client.get("/jobs").json()
        assert any(j["job_id"] == job_id for j in jobs)


# ── Watcher integration (_on_ice_add / _on_ice_remove) ───────────────────────

class TestWatcherCallbacks:
    def test_on_ice_add_registers_recipe(self):
        master = PermafrostMaster(host="127.0.0.1", port=18702)
        recipe = parse_dict(MINIMAL)
        master._on_ice_add(recipe)
        assert "climate" in master.recipes
        assert master.recipes["climate"].codec == "zstd"

    def test_on_ice_add_overwrites_existing(self):
        master = PermafrostMaster(host="127.0.0.1", port=18703)
        r1 = parse_dict({**MINIMAL, "codec": "zstd"})
        r2 = parse_dict({**MINIMAL, "codec": "lzma2"})
        master._on_ice_add(r1)
        master._on_ice_add(r2)
        assert master.recipes["climate"].codec == "lzma2"

    def test_on_ice_remove_deletes_recipe(self):
        master = PermafrostMaster(host="127.0.0.1", port=18704)
        master._on_ice_add(parse_dict(MINIMAL))
        master._on_ice_remove("climate")
        assert "climate" not in master.recipes

    def test_on_ice_remove_missing_name_is_noop(self):
        master = PermafrostMaster(host="127.0.0.1", port=18705)
        master._on_ice_remove("no-such-recipe")  # must not raise

    def test_master_with_watch_path_creates_watcher(self, tmp_path):
        master = PermafrostMaster(
            host="127.0.0.1", port=18706,
            watch_path=str(tmp_path),
            poll_interval=60.0,
        )
        assert master._watcher is not None

    def test_master_without_watch_path_has_no_watcher(self):
        master = PermafrostMaster(host="127.0.0.1", port=18707)
        assert master._watcher is None

    def test_recipes_visible_via_api_after_watcher_add(self):
        master = PermafrostMaster(host="127.0.0.1", port=18708)
        client = TestClient(master.app)
        master._on_ice_add(parse_dict({**MINIMAL, "name": "watcher-r"}))
        r = client.get("/recipes/watcher-r")
        assert r.status_code == 200
        assert r.json()["name"] == "watcher-r"

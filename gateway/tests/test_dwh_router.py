import os, pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET", "test-admin")
    # neutralise la tâche background (la logique scan est testée dans test_scanner.py)
    import gateway.dwh_router as r
    async def _noop(**kwargs): return None
    monkeypatch.setattr(r, "run_scan", _noop)
    app = FastAPI()
    app.include_router(r.router)
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"id": 1, "name": "s1", "db_type": "sqlite",
        "host": None, "port": None, "username": None, "options": {}, "db_filter": [],
        "last_scan_at": None, "last_scan_status": None, "created_at": None}])
    pool.fetchrow = AsyncMock(return_value={"id": 1, "name": "s1", "db_type": "sqlite",
        "host": None, "port": None, "username": None, "options": {}, "db_filter": [],
        "last_scan_at": None, "last_scan_status": None, "created_at": None,
        "status": "running", "password_encrypted": None})
    pool.execute = AsyncMock()
    app.state.db_pool = pool
    app.state.classifier = AsyncMock()
    app.state.column_labels = AsyncMock()
    cfg = MagicMock(); cfg.qwen_auto_approve_threshold = 0.7
    store_cfg = AsyncMock(); store_cfg.get_ner_config = AsyncMock(return_value=cfg)
    app.state.config_store = store_cfg
    return TestClient(app)

H = {"X-Admin-Secret": "test-admin"}

def test_auth_required(client):
    assert client.get("/admin/dwh_sources").status_code == 403

def test_list_sources(client):
    r = client.get("/admin/dwh_sources", headers=H)
    assert r.status_code == 200 and isinstance(r.json(), list)
    assert all("password_encrypted" not in s for s in r.json())

def test_create_source(client):
    r = client.post("/admin/dwh_sources", headers=H, json={"name": "s1", "db_type": "sqlite"})
    assert r.status_code == 200 and r.json()["name"] == "s1"

def test_scan_returns_job_id(client):
    r = client.post("/admin/dwh_sources/1/scan", headers=H, json={"dbs": ["main"]})
    assert r.status_code == 200 and "job_id" in r.json()

def test_scan_status(client):
    r = client.get("/admin/dwh_sources/scan/1", headers=H)
    assert r.status_code == 200 and "status" in r.json()

import pytest
from unittest.mock import AsyncMock, MagicMock
from gateway.config_store import ConfigStore


@pytest.fixture
def store(mock_db_pool):
    return ConfigStore(mock_db_pool)


@pytest.mark.asyncio
async def test_get_ner_config_returns_defaults(store, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={
        "active_labels": ["PERSONNE", "DATE"],
        "gliner_threshold": 0.5,
        "gliner_enabled": True, "presidio_enabled": True, "classifier_enabled": True,
        "deanon_enabled": True, "hook_enabled": True,
        "qwen_auto_approve_threshold": 0.7,
    })
    cfg = await store.get_ner_config()
    assert cfg["active_labels"] == ["PERSONNE", "DATE"]
    assert cfg["gliner_threshold"] == 0.5
    assert cfg["qwen_auto_approve_threshold"] == 0.7


@pytest.mark.asyncio
async def test_update_ner_config(store, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    await store.update_ner_config(
        ["PERSONNE"], 0.7,
        True, True, True, True, True, 0.7,
    )
    mock_db_pool.execute.assert_called_once()
    call_args = mock_db_pool.execute.call_args[0]
    assert "UPDATE ner_config" in call_args[0]


@pytest.mark.asyncio
async def test_list_patterns(store, mock_db_pool):
    mock_db_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "name": "REF", "regex": r"REF-\d+", "entity_label": "REF_SINISTRE",
         "score": 0.9, "active": True, "created_at": "2026-01-01"}
    ])
    patterns = await store.list_patterns()
    assert len(patterns) == 1
    assert patterns[0]["name"] == "REF"


@pytest.mark.asyncio
async def test_create_pattern(store, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={"id": 1})
    result = await store.create_pattern("REF", r"REF-\d+", "REF_SINISTRE", 0.9)
    assert result["id"] == 1


@pytest.mark.asyncio
async def test_delete_pattern(store, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    await store.delete_pattern(1)
    mock_db_pool.execute.assert_called_once()


@pytest.mark.asyncio
async def test_list_api_keys(store, mock_db_pool):
    mock_db_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "user_id": "uuid-1", "label": "Olivier", "active": True, "created_at": "2026-01-01"}
    ])
    keys = await store.list_api_keys()
    assert keys[0]["label"] == "Olivier"


@pytest.mark.asyncio
async def test_create_api_key(store, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={"id": 1, "user_id": "uuid-1"})
    result = await store.create_api_key("Olivier")
    assert result["plain_key"].startswith("anon_")
    assert result["label"] == "Olivier"


@pytest.mark.asyncio
async def test_revoke_api_key(store, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    await store.revoke_api_key(1)
    mock_db_pool.execute.assert_called_once()
    assert "active = false" in mock_db_pool.execute.call_args[0][0].lower()


import os
from fastapi.testclient import TestClient
from fastapi import FastAPI


ADMIN_SECRET = "test-secret"


@pytest.fixture
def admin_app(mock_db_pool):
    os.environ["ADMIN_SECRET"] = ADMIN_SECRET
    from gateway.admin_router import router
    app = FastAPI()
    app.state.db_pool = mock_db_pool
    # Metadata-only gateway: no anonymizer / ner_config in state.
    app.include_router(router)
    return TestClient(app)


def test_admin_requires_secret(admin_app):
    resp = admin_app.get("/admin/config")
    assert resp.status_code == 403


_NER_ROW = {
    "active_labels": ["PERSONNE", "DATE"],
    "gliner_threshold": 0.5,
    "gliner_enabled": True, "presidio_enabled": True, "classifier_enabled": True,
    "deanon_enabled": True, "hook_enabled": True,
    "qwen_auto_approve_threshold": 0.7,
}


def test_admin_get_config(admin_app, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value=_NER_ROW)
    mock_db_pool.fetch = AsyncMock(return_value=[])
    resp = admin_app.get("/admin/config", headers={"X-Admin-Secret": ADMIN_SECRET})
    assert resp.status_code == 200
    data = resp.json()
    assert "active_labels" in data
    assert "gliner_threshold" in data
    assert "qwen_auto_approve_threshold" in data


def test_admin_update_config(admin_app, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    mock_db_pool.fetchrow = AsyncMock(return_value={**_NER_ROW,
        "active_labels": ["PERSONNE"], "gliner_threshold": 0.7,
    })
    mock_db_pool.fetch = AsyncMock(return_value=[])
    resp = admin_app.put(
        "/admin/config",
        json={"active_labels": ["PERSONNE"], "gliner_threshold": 0.7},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status_code == 200


def test_admin_create_pattern(admin_app, mock_db_pool):
    # create_pattern calls fetchrow (→ {"id": 1}), then _rebuild_ner_config
    # calls fetchrow again for get_ner_config (→ ner config row)
    mock_db_pool.fetchrow = AsyncMock(side_effect=[
        {"id": 1},
        _NER_ROW,
        _NER_ROW,
    ])
    mock_db_pool.fetch = AsyncMock(return_value=[])
    mock_db_pool.execute = AsyncMock()
    resp = admin_app.post(
        "/admin/patterns",
        json={"name": "REF", "regex": r"REF-\d+", "entity_label": "REF_SINISTRE", "score": 0.9},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == 1


def test_admin_create_pattern_rejects_redos_regex(admin_app, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock()
    resp = admin_app.post(
        "/admin/patterns",
        json={"name": "EVIL", "regex": r"(a+)+b", "entity_label": "REF", "score": 0.9},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status_code == 400
    assert "ReDoS" in resp.json()["detail"]
    mock_db_pool.fetchrow.assert_not_called()


def test_admin_delete_pattern(admin_app, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    mock_db_pool.fetchrow = AsyncMock(return_value=_NER_ROW)
    mock_db_pool.fetch = AsyncMock(return_value=[])
    resp = admin_app.delete("/admin/patterns/1", headers={"X-Admin-Secret": ADMIN_SECRET})
    assert resp.status_code == 200


def test_admin_list_keys(admin_app, mock_db_pool):
    mock_db_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "user_id": "uuid-1", "label": "Olivier", "active": True, "created_at": "2026-01-01"}
    ])
    resp = admin_app.get("/admin/keys", headers={"X-Admin-Secret": ADMIN_SECRET})
    assert resp.status_code == 200
    assert resp.json()[0]["label"] == "Olivier"


def test_admin_create_key(admin_app, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={"id": 1, "user_id": "uuid-1"})
    resp = admin_app.post(
        "/admin/keys",
        json={"label": "Olivier"},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["plain_key"].startswith("anon_")


def test_admin_revoke_key(admin_app, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    resp = admin_app.delete("/admin/keys/1", headers={"X-Admin-Secret": ADMIN_SECRET})
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"

"""Tests for POST /api/classify_column.

Asserts:
  - happy path returns (label, confidence) from the bound classifier
  - >5 values → 400 BAD REQUEST (cap)
  - the body (column values) is NOT in any captured log record
  - missing auth → 401
"""
from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


USER_KEY = "anon_classify_test_padded_enough_to_resemble_real_value_here"
ADMIN_SECRET = "classify-test-secret"


def _make_pool():
    pool = AsyncMock()

    async def fetchrow(query, *args):
        if "key_hash" in query:
            return {"user_id": "00000000-0000-0000-0000-000000000001"}
        if "ner_config" in query:
            return {
                "active_labels": ["PERSONNE"],
                "gliner_threshold": 0.5,
                "gliner_enabled": True, "presidio_enabled": True, "classifier_enabled": True,
                "deanon_enabled": True, "hook_enabled": True,
                "qwen_auto_approve_threshold": 0.7,
            }
        return None

    pool.fetchrow = fetchrow
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def client():
    os.environ["ADMIN_SECRET"] = ADMIN_SECRET
    os.environ["POSTGRES_DSN"] = "postgresql://fake"

    pool = _make_pool()

    cc = MagicMock()
    cc.classify = AsyncMock(return_value=("PERSONNE", 0.88, None))
    cc.aclose = AsyncMock()

    with patch("gateway.main.asyncpg.create_pool", new_callable=AsyncMock, return_value=pool), \
         patch("gateway.main.ColumnClassifier", return_value=cc):
        from gateway.main import app
        with TestClient(app) as c:
            yield c


def auth():
    return {"Authorization": f"Bearer {USER_KEY}"}


def test_classify_happy_path(client):
    resp = client.post("/api/classify_column", headers=auth(), json={
        "column": "client_nom",
        "sql_type": "varchar",
        "values": ["David Neri", "Marc Dupont"],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == "PERSONNE"
    assert body["confidence"] == 0.88


def test_classify_too_many_values_rejected(client):
    resp = client.post("/api/classify_column", headers=auth(), json={
        "column": "x",
        "sql_type": "varchar",
        "values": ["a", "b", "c", "d", "e", "f"],
    })
    assert resp.status_code == 400
    assert "Max 5" in resp.text or "max 5" in resp.text.lower()


def test_classify_requires_auth(client):
    resp = client.post("/api/classify_column", json={
        "column": "x", "sql_type": "varchar", "values": ["foo"],
    })
    assert resp.status_code in (401, 403)


def test_classify_does_not_log_body(client, caplog):
    """Per the side-channel mitigation contract — values must not appear in logs."""
    pii = "VERYSPECIFIC_PII_STRING_xyz789"
    with caplog.at_level(logging.INFO):
        resp = client.post("/api/classify_column", headers=auth(), json={
            "column": "secret_col",
            "sql_type": "varchar",
            "values": [pii],
        })
    assert resp.status_code == 200
    for record in caplog.records:
        msg = record.getMessage()
        # The PII string itself must never appear in any log line
        assert pii not in msg, f"PII leaked into log: {record.name}: {msg}"
        # And the json-extras dict either — check the record's __dict__ for completeness
        for v in record.__dict__.values():
            if isinstance(v, str):
                assert pii not in v

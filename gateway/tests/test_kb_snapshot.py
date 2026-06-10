"""Tests for GET /api/kb/snapshot.

Asserts:
  - 200 returns a deterministic sha256 version + payload sections
  - If-None-Match echoing the version → 304 with same ETag, empty body
  - Pending column_labels are EXCLUDED (status='active' only)
  - Inactive patterns are EXCLUDED
  - No auth required (it's metadata)
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_pool(active_labels, all_labels, patterns):
    pool = AsyncMock()

    async def fetchrow(query, *args):
        if "ner_config" in query:
            return {
                "active_labels": ["PERSONNE"], "gliner_threshold": 0.5,
                "gliner_enabled": True, "presidio_enabled": True, "classifier_enabled": True,
                "deanon_enabled": True, "hook_enabled": True,
                "qwen_auto_approve_threshold": 0.7,
            }
        return None

    async def fetch(query, *args):
        if "column_labels" in query:
            # Match the WHERE status filter the store uses
            if args and args[0] == "active":
                return active_labels
            return all_labels
        if "custom_patterns" in query:
            return patterns
        return []

    pool.fetchrow = fetchrow
    pool.fetch = fetch
    pool.execute = AsyncMock()
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def client():
    os.environ["ADMIN_SECRET"] = "kb-snapshot-test"
    os.environ["POSTGRES_DSN"] = "postgresql://fake"

    active = [
        {"id": 1, "header_norm": "client_nom", "header_raw": "Client Nom",
         "label": "PERSONNE", "source": "static", "status": "active",
         "confidence": 1.0, "occurrences": 4, "sample_values": None,
         "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"},
    ]
    all_rows = active + [
        {"id": 2, "header_norm": "ref_pending", "header_raw": "Ref Pending",
         "label": "ID", "source": "qwen3", "status": "pending",
         "confidence": 0.55, "occurrences": 1, "sample_values": None,
         "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"},
    ]
    patterns = [
        {"id": 1, "name": "REF_ACTIVE", "regex": r"REF-\d+",
         "entity_label": "REF", "score": 0.9, "active": True,
         "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": 2, "name": "REF_INACTIVE", "regex": r"OLD-\d+",
         "entity_label": "OLD", "score": 0.8, "active": False,
         "created_at": "2026-01-01T00:00:00+00:00"},
    ]
    pool = _make_pool(active, all_rows, patterns)

    cc = MagicMock()
    cc.classify = AsyncMock(return_value=(None, 0.0, None))
    cc.aclose = AsyncMock()

    with patch("gateway.main.asyncpg.create_pool", new_callable=AsyncMock, return_value=pool), \
         patch("gateway.main.ColumnClassifier", return_value=cc):
        from gateway.main import app
        with TestClient(app) as c:
            yield c


def test_snapshot_returns_active_only(client):
    resp = client.get("/api/kb/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body
    assert len(body["version"]) == 64  # sha256 hex
    assert resp.headers["etag"] == body["version"]

    # Only active labels (no 'pending')
    headers = [r["header_norm"] for r in body["column_labels"]]
    assert "client_nom" in headers
    assert "ref_pending" not in headers

    # Only active patterns
    names = [p["name"] for p in body["custom_patterns"]]
    assert "REF_ACTIVE" in names
    assert "REF_INACTIVE" not in names


def test_snapshot_if_none_match_returns_304(client):
    first = client.get("/api/kb/snapshot")
    version = first.json()["version"]
    second = client.get("/api/kb/snapshot", headers={"If-None-Match": version})
    assert second.status_code == 304
    assert second.headers["etag"] == version
    assert second.content == b""


def test_snapshot_stable_version(client):
    """Same KB state → same sha256."""
    v1 = client.get("/api/kb/snapshot").json()["version"]
    v2 = client.get("/api/kb/snapshot").json()["version"]
    assert v1 == v2


def test_snapshot_no_auth_required(client):
    resp = client.get("/api/kb/snapshot")
    assert resp.status_code == 200

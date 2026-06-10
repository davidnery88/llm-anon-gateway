"""End-to-end smoke tests for the sidecar FastAPI app.

Mocks GLiNER + Presidio + the qwen httpx client, drops in fakeredis.
Asserts the same anonymize/deanonymize/mapping contract the legacy
gateway endpoints provided, just via the sidecar's loopback API.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient


def _make_ner():
    ner = MagicMock()
    ner._base_presidio = MagicMock()
    ner._base_presidio.analyze = MagicMock(return_value=[])

    presidio_mock = MagicMock()
    presidio_mock.analyze = MagicMock(return_value=[])
    ner._build_presidio = MagicMock(return_value=presidio_mock)

    ner.gliner = MagicMock()
    ner.gliner.predict_entities = MagicMock(return_value=[
        {"text": "David Neri", "label": "person", "start": 0, "end": 10, "score": 0.9}
    ])

    from sidecar.ner import NEREngine
    ner.detect = lambda text, config=None: NEREngine.detect(ner, text, config)
    return ner


@pytest.fixture
def sidecar_client():
    os.environ["REDIS_URL"] = "redis://fake"
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    ner = _make_ner()

    cc = MagicMock()
    cc.classify = AsyncMock(return_value=(None, 0.0))
    cc.aclose = AsyncMock()

    with patch("sidecar.main.redis.Redis.from_url", return_value=fake_redis), \
         patch("sidecar.anonymizer.NEREngine", return_value=ner), \
         patch("sidecar.anonymizer.ColumnClassifier", return_value=cc):
        from sidecar.main import app
        with TestClient(app) as client:
            yield client


def test_healthz(sidecar_client):
    resp = sidecar_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_anonymize_freetext(sidecar_client):
    resp = sidecar_client.post("/anonymize", json={"text": "David Neri a un sinistre."})
    assert resp.status_code == 200
    data = resp.json()
    assert "David Neri" not in data["anonymized_text"]
    assert "[PERSONNE_1]" in data["anonymized_text"]
    assert data["mapping"]["[PERSONNE_1]"] == "David Neri"


def test_deanonymize_roundtrip(sidecar_client):
    sidecar_client.post("/anonymize", json={"text": "David Neri a un sinistre."})
    resp = sidecar_client.post("/deanonymize", json={"text": "[PERSONNE_1] a un sinistre."})
    assert resp.status_code == 200
    assert resp.json()["result"] == "David Neri a un sinistre."


def test_get_mapping_after_anonymize(sidecar_client):
    sidecar_client.post("/anonymize", json={"text": "David Neri a un sinistre."})
    resp = sidecar_client.get("/mapping")
    assert resp.status_code == 200
    assert resp.json() == {"[PERSONNE_1]": "David Neri"}


def test_clear_mapping(sidecar_client):
    sidecar_client.post("/anonymize", json={"text": "David Neri a un sinistre."})
    resp = sidecar_client.delete("/mapping")
    assert resp.status_code == 200
    assert resp.json() == {"status": "cleared"}
    assert sidecar_client.get("/mapping").json() == {}


def test_no_admin_auth_required(sidecar_client):
    """Sidecar is loopback-only — no Bearer / no X-Admin-Secret needed in phase 1."""
    resp = sidecar_client.post("/anonymize", json={"text": "anything"})
    assert resp.status_code == 200


def test_anonymize_fail_safe_on_ner_crash(sidecar_client):
    """Si le NER crashe, /anonymize retourne 503 au lieu de laisser passer le PII."""
    from sidecar.main import app
    original_anonymize = app.state.anonymizer.anonymize

    async def _crash(*args, **kwargs):
        raise RuntimeError("GLiNER OOM")

    app.state.anonymizer.anonymize = _crash
    try:
        resp = sidecar_client.post("/anonymize", json={"text": "David Neri"})
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["type"] == "anonymization_error"
        assert "fail-safe" in body["error"]["message"]
    finally:
        app.state.anonymizer.anonymize = original_anonymize

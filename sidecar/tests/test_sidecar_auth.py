"""Verify the optional X-Sidecar-Token auth on the sidecar.

When ANON_SIDECAR_TOKEN is set in the env, every endpoint except
/healthz must require a matching X-Sidecar-Token header.
"""
import importlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_token():
    os.environ["REDIS_URL"] = "redis://fake"
    os.environ["ANON_SIDECAR_TOKEN"] = "s3cret"

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    ner = MagicMock()
    ner._base_presidio = MagicMock()
    ner._build_presidio = MagicMock(return_value=MagicMock())
    ner.gliner = MagicMock()
    ner.gliner.predict_entities = MagicMock(return_value=[])
    from sidecar.ner import NEREngine
    ner.detect = lambda text, config=None: NEREngine.detect(ner, text, config)

    cc = MagicMock()
    cc.classify = AsyncMock(return_value=(None, 0.0))
    cc.aclose = AsyncMock()

    # Re-import sidecar.main so the module-level _AUTH_TOKEN picks up the env
    import sidecar.main as sm
    importlib.reload(sm)

    with patch("sidecar.main.redis.Redis.from_url", return_value=fake_redis), \
         patch("sidecar.anonymizer.NEREngine", return_value=ner), \
         patch("sidecar.anonymizer.ColumnClassifier", return_value=cc):
        with TestClient(sm.app) as c:
            yield c

    os.environ.pop("ANON_SIDECAR_TOKEN", None)
    importlib.reload(sm)  # restore


def test_healthz_works_without_token(client_with_token):
    resp = client_with_token.get("/healthz")
    assert resp.status_code == 200


def test_anonymize_requires_token(client_with_token):
    resp = client_with_token.post("/anonymize", json={"text": "x"})
    assert resp.status_code == 401


def test_anonymize_wrong_token_rejected(client_with_token):
    resp = client_with_token.post(
        "/anonymize", json={"text": "x"},
        headers={"X-Sidecar-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_anonymize_correct_token_accepted(client_with_token):
    resp = client_with_token.post(
        "/anonymize", json={"text": "Bonjour."},
        headers={"X-Sidecar-Token": "s3cret"},
    )
    assert resp.status_code == 200


def test_mapping_requires_token(client_with_token):
    resp = client_with_token.get("/mapping")
    assert resp.status_code == 401
    resp = client_with_token.get("/mapping", headers={"X-Sidecar-Token": "s3cret"})
    assert resp.status_code == 200


def test_cors_preflight_allows_localhost(client_with_token):
    resp = client_with_token.options(
        "/anonymize",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"

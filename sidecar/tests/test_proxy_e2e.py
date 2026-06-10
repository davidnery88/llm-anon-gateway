"""Test end-to-end du proxy /v1/messages.

Vérifie le tunnel complet :
  Claude Code → POST /v1/messages (sidecar) → anonymise → Anthropic (mocké) → désanonymise → Claude Code

Ce que le test prouve :
  1. Le PII dans les messages (ex. "David Neri") est remplacé avant le forward.
  2. Le placeholder ([PERSONNE_1]) dans la réponse Anthropic est remplacé avant
     d'être retourné à Claude Code.
  3. Si l'anonymisation crashe, le proxy bloque la requête (fail-safe, 503).
  4. Un token Authorization est bien forwardé vers Anthropic tel quel.
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ner():
    """Mock NER : détecte toujours 'David Neri' comme PERSONNE."""
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


def _sse_stream(*text_deltas: str) -> bytes:
    """Construit un flux SSE Anthropic minimal avec les text_delta donnés."""
    events: list[dict] = [
        {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant",
            "content": [], "model": "claude-opus-4-8",
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
    ]
    for delta in text_deltas:
        events.append({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": delta},
        })
    events += [
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta",
         "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": len(text_deltas)}},
        {"type": "message_stop"},
    ]
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode()
        for e in events
    )


def _mock_anthropic(response_sse: bytes, captured_bodies: list[bytes]):
    """Renvoie un patch de httpx.AsyncClient qui :
    - capture le body forwardé dans captured_bodies
    - retourne response_sse comme réponse streamée
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    mock_resp.aclose = AsyncMock()

    async def _aiter_raw():
        yield response_sse

    mock_resp.aiter_raw = _aiter_raw

    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()

    def _build_request(method, url, headers, content):
        captured_bodies.append(content)
        return MagicMock()

    mock_client.build_request = _build_request
    mock_client.send = AsyncMock(return_value=mock_resp)

    return MagicMock(return_value=mock_client)


def _extract_text_deltas(sse_bytes: bytes) -> str:
    """Concatène tous les delta.text dans un flux SSE."""
    out = []
    for block in sse_bytes.split(b"\n\n"):
        for line in block.split(b"\n"):
            if not line.startswith(b"data: "):
                continue
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if (isinstance(payload, dict)
                    and payload.get("type") == "content_block_delta"):
                delta = payload.get("delta", {})
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    out.append(delta.get("text", ""))
    return "".join(out)


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture
def proxy_client():
    """Client sidecar avec NER mocké, Redis fakeredis, Anthropic mocké.
    captured_bodies[0] = body JSON forwardé vers Anthropic (après anonymisation).
    """
    os.environ["REDIS_URL"] = "redis://fake"
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    ner = _make_ner()
    cc = MagicMock()
    cc.classify = AsyncMock(return_value=(None, 0.0))
    cc.aclose = AsyncMock()

    with (
        patch("sidecar.main.redis.Redis.from_url", return_value=fake_redis),
        patch("sidecar.anonymizer.NEREngine", return_value=ner),
        patch("sidecar.anonymizer.ColumnClassifier", return_value=cc),
    ):
        from sidecar.main import app
        with TestClient(app) as client:
            yield client


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_proxy_anonymizes_outbound(proxy_client):
    """Le PII dans le message user est remplacé par un placeholder avant le forward."""
    captured: list[bytes] = []
    sse = _sse_stream("Je peux vous aider.")

    with patch("sidecar.proxy.httpx.AsyncClient", _mock_anthropic(sse, captured)):
        resp = proxy_client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer sk-test", "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Aide David Neri avec son dossier."}],
            },
        )

    assert resp.status_code == 200
    assert len(captured) == 1
    forwarded = json.loads(captured[0])
    user_content = forwarded["messages"][0]["content"]
    assert "David Neri" not in user_content, "PII ne doit pas partir vers Anthropic"
    assert "[PERSONNE_1]" in user_content, "Le placeholder doit être dans le body forwardé"


def test_proxy_rejects_image_block_with_400(proxy_client):
    """Un bloc image n'est pas anonymisable : 400 explicite, rien ne part vers Anthropic."""
    captured: list[bytes] = []
    sse = _sse_stream("ok")

    with patch("sidecar.proxy.httpx.AsyncClient", _mock_anthropic(sse, captured)):
        resp = proxy_client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer sk-test", "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                ]}],
            },
        )

    assert resp.status_code == 400
    assert "image" in resp.json()["error"]["message"]
    assert captured == [], "Rien ne doit partir vers Anthropic"


def test_proxy_deanonymizes_inbound(proxy_client):
    """Le placeholder dans la réponse Anthropic est remplacé par la vraie valeur."""
    # D'abord on anonymise pour créer le mapping Redis
    proxy_client.post("/anonymize", json={"text": "David Neri a un sinistre."})

    captured: list[bytes] = []
    # Anthropic retourne [PERSONNE_1] dans sa réponse (il a vu le placeholder)
    sse = _sse_stream("Voici le dossier de [PERSONNE_1].")

    with patch("sidecar.proxy.httpx.AsyncClient", _mock_anthropic(sse, captured)):
        resp = proxy_client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer sk-test", "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Résume le dossier."}],
            },
        )

    assert resp.status_code == 200
    visible = _extract_text_deltas(resp.content)
    assert "[PERSONNE_1]" not in visible, "Le placeholder ne doit pas atteindre Claude Code"
    assert "David Neri" in visible, "La vraie valeur doit être restituée"


def test_proxy_forwards_auth_header(proxy_client):
    """Le token Authorization (Teams OAuth ou API key) est forwardé tel quel."""
    captured: list[bytes] = []
    forwarded_headers: list[dict] = []
    sse = _sse_stream("ok")

    original_mock = _mock_anthropic(sse, captured)
    original_build = original_mock.return_value.build_request

    def _capturing_build(method, url, headers, content):
        forwarded_headers.append(dict(headers))
        captured.append(content)
        return MagicMock()

    original_mock.return_value.build_request = _capturing_build

    with patch("sidecar.proxy.httpx.AsyncClient", original_mock):
        proxy_client.post(
            "/v1/messages",
            headers={
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.test",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Bonjour"}],
            },
        )

    assert len(forwarded_headers) == 1
    auth = forwarded_headers[0].get("authorization", "")
    assert "eyJhbGciOiJIUzI1NiJ9.test" in auth, "Le token doit être forwardé verbatim"


def test_proxy_failsafe_on_anonymization_crash(proxy_client):
    """Si l'anonymisation crashe, le proxy retourne 503 — jamais le PII en clair."""
    from sidecar.main import app

    original_anon = app.state.payload_anonymizer.anonymize_body

    async def _crash(body):
        raise RuntimeError("NER OOM")

    app.state.payload_anonymizer.anonymize_body = _crash
    try:
        resp = proxy_client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer sk-test", "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "David Neri SSN 756.1234.5678.90"}],
            },
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["type"] == "anonymization_error"
    finally:
        app.state.payload_anonymizer.anonymize_body = original_anon


def test_proxy_upstream_error_returns_502(proxy_client):
    """Si Anthropic est injoignable, le proxy retourne 502."""
    import httpx as _httpx

    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()
    mock_client.build_request = MagicMock(return_value=MagicMock())
    mock_client.send = AsyncMock(side_effect=_httpx.ConnectError("unreachable"))

    with patch("sidecar.proxy.httpx.AsyncClient", MagicMock(return_value=mock_client)):
        resp = proxy_client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer sk-test", "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Bonjour"}],
            },
        )

    assert resp.status_code == 502

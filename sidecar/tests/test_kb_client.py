"""Unit tests for KBClient (pull, ETag 304, disk cache, refresh)."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    p = tmp_path / "kb.json"
    monkeypatch.setenv("ANON_SIDECAR_CACHE", str(p))
    # Re-import to pick up the env override (CACHE_PATH is module-scoped)
    import importlib
    import sidecar.kb_client as mod
    importlib.reload(mod)
    yield p
    importlib.reload(mod)  # restore


def _fake_response(status, json_body=None, headers=None):
    resp = AsyncMock()
    resp.status_code = status
    resp.json = lambda: json_body
    resp.headers = headers or {}
    return resp


@pytest.mark.asyncio
async def test_pull_success_updates_state(cache_path):
    from sidecar.kb_client import KBClient
    payload = {
        "version": "abc123" * 10,
        "column_labels": [{"header_norm": "x", "header_raw": "x", "label": "PERSONNE", "source": "static", "confidence": 1.0}],
        "custom_patterns": [],
        "hook_enabled": True,
        "deanon_enabled": True,
    }
    client = KBClient(gateway_url="http://fake", api_key="anon_test")
    with patch.object(client._client, "get", new_callable=AsyncMock,
                       return_value=_fake_response(200, payload)):
        updated = await client.pull()
    assert updated is True
    assert client.version == payload["version"]
    assert client.header_to_label("x") == "PERSONNE"
    assert cache_path.exists()


@pytest.mark.asyncio
async def test_pull_304_keeps_state(cache_path):
    from sidecar.kb_client import KBClient
    client = KBClient(gateway_url="http://fake", api_key="anon_test")
    client._version = "stable_etag_v1"
    client._snapshot = {"column_labels": [], "version": "stable_etag_v1"}

    with patch.object(client._client, "get", new_callable=AsyncMock,
                       return_value=_fake_response(304)) as mock_get:
        updated = await client.pull()
    assert updated is False
    assert client.version == "stable_etag_v1"
    # Confirm If-None-Match was sent
    called_headers = mock_get.call_args.kwargs["headers"]
    assert called_headers["If-None-Match"] == "stable_etag_v1"


@pytest.mark.asyncio
async def test_load_from_disk_cold_start(cache_path):
    payload = {
        "version": "from_disk",
        "column_labels": [{"header_norm": "y", "header_raw": "y", "label": "EMAIL", "source": "static", "confidence": 1.0}],
        "custom_patterns": [],
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))

    from sidecar.kb_client import KBClient
    client = KBClient(gateway_url="http://fake", api_key="")
    assert client.load_from_disk() is True
    assert client.version == "from_disk"
    assert client.header_to_label("y") == "EMAIL"


@pytest.mark.asyncio
async def test_load_from_disk_missing_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("ANON_SIDECAR_CACHE", str(tmp_path / "nonexistent.json"))
    import importlib
    import sidecar.kb_client as mod
    importlib.reload(mod)
    client = mod.KBClient(gateway_url="http://fake", api_key="")
    assert client.load_from_disk() is False
    assert client.snapshot is None


@pytest.mark.asyncio
async def test_pull_http_error_does_not_crash(cache_path):
    import httpx
    from sidecar.kb_client import KBClient
    client = KBClient(gateway_url="http://fake", api_key="")
    with patch.object(client._client, "get", new_callable=AsyncMock,
                       side_effect=httpx.ConnectError("boom")):
        updated = await client.pull()
    assert updated is False
    assert client.snapshot is None

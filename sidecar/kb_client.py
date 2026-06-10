"""Sidecar KB synchronization client.

Pulls the KB snapshot (column_labels + custom_patterns + flags) from the
LAN gateway and exposes it as a read-only in-memory dict to the rest of
the sidecar.

Refresh strategy:
  - Pull on startup
  - Refresh every REFRESH_INTERVAL seconds in a background asyncio.Task
  - Manual `POST /refresh` on the sidecar triggers an immediate pull
  - On-disk cache at ~/.cache/anon-sidecar/kb.json — used when the gateway
    is briefly unreachable at boot so anonymization keeps working
  - Conditional GET with If-None-Match (server returns 304 → no-op)

The KB transit is metadata only — column header names + entity labels +
regex patterns. Zero PII.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

from sidecar.logging_config import get as _log

_logger = _log("sidecar.kb_client")

DEFAULT_REFRESH_INTERVAL = 600  # 10 min
CACHE_PATH = Path(os.environ.get(
    "ANON_SIDECAR_CACHE", str(Path.home() / ".cache" / "anon-sidecar" / "kb.json")
))


class KBClient:
    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
        oauth_client=None,
    ):
        self._gateway_url = gateway_url.rstrip("/")
        self._api_key = api_key
        self._oauth_client = oauth_client  # OAuthClient | None
        self._refresh_interval = refresh_interval
        self._client = httpx.AsyncClient(timeout=10.0)
        self._version: str | None = None
        self._snapshot: dict | None = None
        self._task: asyncio.Task | None = None

    async def _auth_headers(self) -> dict:
        """OAuth en priorité, fallback sur bearer key legacy."""
        if self._oauth_client is not None:
            return await self._oauth_client.auth_header()
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    @property
    def snapshot(self) -> dict | None:
        return self._snapshot

    @property
    def version(self) -> str | None:
        return self._version

    def header_to_label(self, header_norm: str) -> str | None:
        """Lookup utility used by the sidecar anonymizer."""
        if not self._snapshot:
            return None
        for row in self._snapshot.get("column_labels", []):
            if row["header_norm"] == header_norm:
                return row["label"]
        return None

    async def pull(self) -> bool:
        """One synchronous pull. Returns True on update, False on 304/no-op."""
        headers = await self._auth_headers()
        if self._version:
            headers["If-None-Match"] = self._version

        try:
            resp = await self._client.get(f"{self._gateway_url}/api/kb/snapshot", headers=headers)
        except httpx.HTTPError as exc:
            _logger.warning("kb.pull.failed", extra={"err": str(exc)})
            return False

        if resp.status_code == 304:
            _logger.info("kb.pull.304", extra={"version": self._version[:12] if self._version else None})
            return False
        if resp.status_code != 200:
            _logger.warning("kb.pull.http_error", extra={"status": resp.status_code})
            return False

        data = resp.json()
        self._snapshot = data
        self._version = data["version"]
        self._persist()
        _logger.info(
            "kb.pull.updated",
            extra={
                "version": self._version[:12],
                "n_labels": len(data.get("column_labels", [])),
                "n_patterns": len(data.get("custom_patterns", [])),
            },
        )
        return True

    def _persist(self) -> None:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(self._snapshot))
        except OSError as exc:
            _logger.warning("kb.cache.persist_failed", extra={"err": str(exc)})

    def load_from_disk(self) -> bool:
        """Cold-start fallback when the gateway is unreachable."""
        if not CACHE_PATH.exists():
            return False
        try:
            self._snapshot = json.loads(CACHE_PATH.read_text())
            self._version = self._snapshot.get("version")
            _logger.info("kb.cache.loaded", extra={"version": self._version[:12] if self._version else None})
            return True
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning("kb.cache.load_failed", extra={"err": str(exc)})
            return False

    async def start_background_refresh(self) -> None:
        """Spawn the periodic poller. Idempotent."""
        if self._task is not None:
            return

        async def _loop():
            while True:
                await asyncio.sleep(self._refresh_interval)
                try:
                    await self.pull()
                except Exception as exc:
                    _logger.warning("kb.refresh.crash", extra={"err": str(exc)})

        self._task = asyncio.create_task(_loop(), name="kb-refresh")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self._client.aclose()

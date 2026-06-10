from __future__ import annotations

import asyncio

import httpx

from sidecar.logging_config import get as _log

_logger = _log("sidecar.audit_client")


class AuditClient:
    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        oauth_client=None,
    ):
        self._gateway_url = gateway_url.rstrip("/")
        self._api_key = api_key
        self._oauth_client = oauth_client
        self._client = httpx.AsyncClient(timeout=5.0)

    async def _auth_headers(self) -> dict:
        if self._oauth_client is not None:
            return await self._oauth_client.auth_header()
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def record(self, event: dict) -> None:
        async def _send():
            try:
                headers = await self._auth_headers()
                headers["Content-Type"] = "application/json"
                resp = await self._client.post(
                    f"{self._gateway_url}/api/audit",
                    json=event,
                    headers=headers,
                )
                if resp.status_code != 204:
                    _logger.warning(
                        "audit.send_failed",
                        extra={"status": resp.status_code, "body": resp.text[:200]},
                    )
            except Exception as exc:
                _logger.warning("audit.send_error", extra={"err": str(exc)})

        asyncio.ensure_future(_send())

    async def aclose(self) -> None:
        await self._client.aclose()

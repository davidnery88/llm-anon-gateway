"""Phase 4.6 — client OAuth 2.0 client_credentials pour appeler le gateway.

Récupère un access token via POST /oauth/token (RFC 6749 §4.4) au démarrage,
cache le token, et le refresh proactivement à ~80% du TTL annoncé. Thread-safe
via asyncio.Lock.

Config par env vars :
- OAUTH_TOKEN_URL    : URL du endpoint /oauth/token (ex http://gateway:8000/oauth/token)
- OAUTH_CLIENT_ID    : identifiant client (créé en DB côté gateway dans la
                       table oauth_clients)
- OAUTH_CLIENT_SECRET: secret partagé (hashé côté gateway en DB)
- OAUTH_SCOPE        : optionnel, scopes demandés (séparés par espace)

Si OAUTH_TOKEN_URL est absent, le client tombe sur le bearer key legacy via
GATEWAY_API_KEY (rétrocompat sans casser l'existant).
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx

from sidecar.logging_config import get as _log

_logger = _log("sidecar.oauth_client")

# Refresh à 80% du TTL — laisse 20% de marge pour absorber latence + horloge décalée
REFRESH_RATIO = 0.8


class OAuthClient:
    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
    ):
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._token: str | None = None
        self._refresh_at: float = 0  # epoch seconds
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=10.0)

    @classmethod
    def from_env(cls) -> "OAuthClient | None":
        """Construit le client depuis l'env. Retourne None si l'OAuth n'est
        pas configuré → caller fallback sur GATEWAY_API_KEY legacy."""
        token_url = os.environ.get("OAUTH_TOKEN_URL", "").strip()
        client_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
        client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
        if not (token_url and client_id and client_secret):
            return None
        return cls(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scope=os.environ.get("OAUTH_SCOPE") or None,
        )

    async def get_token(self) -> str:
        """Retourne un access token valide. Refresh si proche d'expirer."""
        if self._token and time.time() < self._refresh_at:
            return self._token

        async with self._lock:
            # Re-check sous lock pour éviter double-fetch concurrent
            if self._token and time.time() < self._refresh_at:
                return self._token
            await self._refresh()
            return self._token  # type: ignore[return-value]

    async def _refresh(self) -> None:
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scope:
            data["scope"] = self._scope

        try:
            resp = await self._http.post(self._token_url, data=data)
        except httpx.HTTPError as e:
            _logger.error("oauth.fetch_failed", extra={"err": str(e)})
            raise

        if resp.status_code != 200:
            _logger.error("oauth.fetch_bad_status", extra={
                "status": resp.status_code, "body": resp.text[:200],
            })
            resp.raise_for_status()

        payload = resp.json()
        self._token = payload["access_token"]
        ttl = int(payload.get("expires_in", 3600))
        self._refresh_at = time.time() + ttl * REFRESH_RATIO
        _logger.info("oauth.refreshed", extra={
            "ttl_seconds": ttl, "scope": payload.get("scope"),
        })

    async def auth_header(self) -> dict:
        """Shortcut : retourne {'Authorization': 'Bearer ...'} prêt à concaténer."""
        return {"Authorization": f"Bearer {await self.get_token()}"}

    async def aclose(self) -> None:
        await self._http.aclose()

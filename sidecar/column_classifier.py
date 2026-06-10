"""Sidecar column classifier — délégué au gateway.

Côté gateway legacy, ce module appelait Ollama directement. Côté sidecar
(modèle zero-trust), qwen3-pii reste sur le serveur — on l'appelle via
HTTP au endpoint `/api/classify_column`. C'est le "side-channel PII"
assumé (max 5 valeurs, no body logging, rate limit côté serveur).

L'interface publique (`classify(table, column, sql_type, values)`) reste
identique au module legacy pour que `sidecar/anonymizer.py` n'ait rien à
savoir de la migration.
"""
import hashlib
import os
import re

import httpx

from sidecar.logging_config import get as _log

_logger = _log("sidecar.classifier")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_DIGITS_RE = re.compile(r"^[0-9]+$")
_ALPHA_RE = re.compile(r"^[a-zA-Z]+$")
_ALPHANUM_RE = re.compile(r"^[a-zA-Z0-9]+$")

_REGEX_HINTS = [
    (re.compile(r"^[0-9]{13}$"), "avs"),
    (re.compile(r"^(CH|LI)[0-9]{2}[0-9]{5}[A-Za-z0-9]{5,17}$"), "iban_ch"),
    (re.compile(r"^(\+41|0041)?[0-9]{2,3}\s?[0-9]{3}\s?[0-9]{2}\s?[0-9]{2}$"), "phone"),
    (re.compile(r"^[0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4}$"), "date"),
    (re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"), "date"),
    (re.compile(r"^[0-9]{2}:[0-9]{2}(:[0-9]{2})?$"), "date"),
]


def _classify_charset(value: str) -> str:
    if _EMAIL_RE.match(value):
        return "email"
    if _URL_RE.match(value):
        return "url"
    if _DIGITS_RE.match(value):
        return "digits"
    if _ALPHA_RE.match(value):
        return "alpha"
    if _ALPHANUM_RE.match(value):
        return "alphanum"
    return "mixed"


def _classify_regex_hint(value: str) -> str:
    for pattern, hint in _REGEX_HINTS:
        if pattern.match(value):
            return hint
    return "none"


def _value_metadata(value: str) -> dict:
    return {
        "length": len(value),
        "charset": _classify_charset(value),
        "has_spaces": " " in value,
        "has_punctuation": bool(re.search(r"[.,;:!?'\-/@&]", value)),
        "regex_hint": _classify_regex_hint(value),
        "sample_hash": hashlib.sha256(value.encode("utf-8")).hexdigest()[:8],
    }


class ColumnClassifier:
    def __init__(self, ollama_url: str = "", oauth_client=None):
        # ollama_url is ignored (kept for backward-compat with anonymizer ctor).
        gateway_url = os.environ.get("GATEWAY_URL", "http://localhost:8001").rstrip("/")
        self._api_key = os.environ.get("GATEWAY_API_KEY", "")
        self._oauth_client = oauth_client
        self._endpoint = f"{gateway_url}/api/classify_column"
        # Auth header injecté à chaque appel (le token OAuth peut être refreshed
        # entre deux requêtes, contrairement à un API key statique).
        self.client = httpx.AsyncClient(timeout=15.0)

    async def _auth_headers(self) -> dict:
        if self._oauth_client is not None:
            return await self._oauth_client.auth_header()
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def classify(self, table, column, sql_type, values):
        sample = values[:5]
        metadata = [_value_metadata(str(v)) for v in sample]
        try:
            resp = await self.client.post(
                self._endpoint,
                json={"table": table, "column": column, "sql_type": sql_type, "value_metadata": metadata},
                headers=await self._auth_headers(),
            )
        except httpx.TimeoutException:
            _logger.warning("classifier.timeout", extra={"column": column})
            return None, 0.0
        except httpx.HTTPError as exc:
            _logger.warning("classifier.http_error", extra={"column": column, "err": str(exc)})
            return None, 0.0

        if resp.status_code != 200:
            _logger.warning("classifier.bad_status", extra={"column": column, "status": resp.status_code})
            return None, 0.0

        data = resp.json()
        label = data.get("label")
        confidence = float(data.get("confidence", 0.0))
        _logger.info(
            "classifier.result",
            extra={
                "column":     column,
                "label":      label,
                "confidence": confidence,
            },
        )
        return label, confidence

    async def aclose(self):
        await self.client.aclose()

# Copyright (c) 2026 David Miguel Loureiro Neri <david@neri.contact>
# Licensed under the PolyForm Noncommercial License 1.0.0.
# See LICENSE file for details.

"""MCP server: expose query_db (SELECT-only avec anonymisation transparente),
un toggle live de l'anonymisation, et un diagnostic status — pour la démo.

Architecture zero-trust : le MCP server tape le **sidecar local** (sur
127.0.0.1), pas un gateway distant. Aucune PII ne sort de la machine.

Configuration via variables d'environnement (ou .env dans le répertoire du script) :
  SIDECAR_URL=http://127.0.0.1:8787       # défaut, ne pas changer en prod
  DEMO_DB_PATH=/abs/path/to/demo.sqlite

Toggle anonymisation (pour la démo "avant/après") :
  - fichier flag : ~/.config/anon-gateway/disabled (touch pour OFF, rm pour ON)
  - tool MCP    : set_anonymization(enabled: bool)
  - env var     : ANON_DISABLED=1 (override permanent, ignore le flag)

Ajout dans .claude/settings.local.json :
{
  "mcpServers": {
    "anon-gateway": {
      "command": "/abs/path/to/venv/bin/python",
      "args": ["/abs/path/to/mcp_server/server.py"],
      "env": {
        "SIDECAR_URL": "http://127.0.0.1:8787",
        "DEMO_DB_PATH": "/abs/path/to/demo/demo.sqlite"
      }
    }
  }
}

Pour désactiver le MCP entier sans le supprimer : ajouter "disabled": true à l'entrée,
ou commenter le bloc.
"""
from __future__ import annotations
import asyncio
import csv
import io
import os
import re
import sqlite3
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

SIDECAR_URL = os.environ.get("SIDECAR_URL", "http://127.0.0.1:8787")
SIDECAR_TOKEN_PATH = Path(os.environ.get(
    "ANON_SIDECAR_TOKEN_PATH",
    str(Path.home() / ".config" / "anon-sidecar" / "token"),
))

OAUTH_TOKEN_URL = os.environ.get("OAUTH_TOKEN_URL", "").strip()
OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "").strip()
OAUTH_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
GATEWAY_URL = os.environ.get("GATEWAY_URL", "").strip()

REFRESH_RATIO = 0.8


class _SidecarTokenManager:
    def __init__(self):
        self._token: str | None = None
        self._refresh_at: float = 0
        self._lock = asyncio.Lock()
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def get_token(self) -> str:
        if self._token and time.time() < self._refresh_at:
            return self._token

        async with self._lock:
            if self._token and time.time() < self._refresh_at:
                return self._token
            await self._refresh()
            return self._token  # type: ignore[return-value]

    async def _refresh(self) -> None:
        try:
            client = await self._get_http()
            resp = await client.post(
                OAUTH_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": OAUTH_CLIENT_ID,
                    "client_secret": OAUTH_CLIENT_SECRET,
                    "scope": "sidecar:token",
                },
            )
        except httpx.HTTPError:
            if self._token:
                return
            raise

        if resp.status_code != 200:
            if self._token:
                return
            resp.raise_for_status()

        oauth_payload = resp.json()
        access_token = oauth_payload["access_token"]

        try:
            resp2 = await client.post(
                f"{GATEWAY_URL}/oauth/sidecar_token",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError:
            if self._token:
                return
            raise

        if resp2.status_code != 200:
            if self._token:
                return
            resp2.raise_for_status()

        payload = resp2.json()
        self._token = payload["sidecar_token"]
        ttl = int(payload.get("expires_in", 3600))
        self._refresh_at = time.time() + ttl * REFRESH_RATIO

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()


_oauth_manager: _SidecarTokenManager | None = None

if OAUTH_TOKEN_URL and OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET and GATEWAY_URL:
    _oauth_manager = _SidecarTokenManager()


def _load_sidecar_token() -> str:
    try:
        return SIDECAR_TOKEN_PATH.read_text().strip()
    except (FileNotFoundError, OSError):
        return ""


_LEGACY_TOKEN = _load_sidecar_token()


async def _sidecar_headers() -> dict:
    if _oauth_manager is not None:
        try:
            token = await _oauth_manager.get_token()
            if token:
                return {"X-Sidecar-Token": token}
        except Exception:
            pass
    return {"X-Sidecar-Token": _LEGACY_TOKEN} if _LEGACY_TOKEN else {}


DEMO_DB_PATH = os.environ.get("DEMO_DB_PATH", "")
ENV_DISABLED = os.environ.get("ANON_DISABLED", "").strip().lower() in ("1", "true", "yes")

FLAG_PATH = Path(os.environ.get("ANON_FLAG_PATH", str(Path.home() / ".config" / "anon-gateway" / "disabled")))

mcp = FastMCP("anon-gateway")

# SQL allowlist : on accepte uniquement SELECT (et WITH...SELECT). Tout le reste est refusé.
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|attach|detach|pragma|vacuum|replace|reindex)\b",
    re.IGNORECASE,
)
_ALLOWED_START_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def _anonymization_disabled() -> bool:
    """True si l'anonymisation est désactivée (env var OU fichier flag présent)."""
    if ENV_DISABLED:
        return True
    return FLAG_PATH.exists()


def _rows_to_csv(columns: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows([[("" if v is None else str(v)) for v in r] for r in rows])
    return buf.getvalue()


def _validate_sql(sql: str) -> str | None:
    """Retourne un message d'erreur si la requête n'est pas une lecture seule, sinon None."""
    if not sql or not sql.strip():
        return "Requête SQL vide."
    if ";" in sql.strip().rstrip(";"):
        return "Plusieurs instructions SQL détectées — une seule requête par appel."
    if not _ALLOWED_START_RE.match(sql):
        return "Seules les requêtes SELECT (ou WITH ... SELECT) sont autorisées."
    if _FORBIDDEN_RE.search(sql):
        return "Mot-clé d'écriture / DDL détecté — interdit en mode lecture seule."
    return None


@mcp.tool()
async def query_db(sql: str, max_rows: int = 200) -> dict:
    """Exécute une requête SELECT sur la base de démo et retourne les résultats
    DÉJÀ ANONYMISÉS (les PII ne sont jamais exposées à l'agent appelant).

    Args:
        sql: requête SELECT (DDL/DML interdits — refus immédiat).
        max_rows: nombre max de lignes retournées (défaut 200).

    Returns:
        {
            "columns": [...],
            "row_count": N,
            "anonymized_csv": "...",          # CSV avec tokens [PERSONNE_1] etc.
            "mapping_count": K,               # nombre de tokens générés (mapping reste côté gateway)
            "anonymization_enabled": bool,    # False si le toggle démo est OFF
            "warning": "..."                  # présent uniquement si anonymisation désactivée
        }
    """
    if not DEMO_DB_PATH:
        return {"error": "DEMO_DB_PATH non configuré dans l'environnement du MCP."}
    if not Path(DEMO_DB_PATH).exists():
        return {"error": f"DB introuvable : {DEMO_DB_PATH}"}

    err = _validate_sql(sql)
    if err:
        return {"error": err}

    try:
        conn = sqlite3.connect(f"file:{DEMO_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        rows = cur.fetchmany(max_rows)
        columns = [d[0] for d in cur.description] if cur.description else []
        conn.close()
    except sqlite3.Error as e:
        return {"error": f"Erreur SQL : {e}"}

    csv_text = _rows_to_csv(columns, [list(r) for r in rows])

    if _anonymization_disabled():
        return {
            "columns": columns,
            "row_count": len(rows),
            "anonymized_csv": csv_text,  # en réalité PAS anonymisé — c'est le mode démo OFF
            "mapping_count": 0,
            "anonymization_enabled": False,
            "warning": "⚠️  ANONYMISATION DÉSACTIVÉE — les PII sont visibles en clair. Active avec set_anonymization(true) ou supprime le fichier flag.",
        }

    async with httpx.AsyncClient(base_url=SIDECAR_URL, timeout=60, headers=await _sidecar_headers()) as client:
        resp = await client.post("/anonymize", json={"text": csv_text})
        if resp.status_code != 200:
            return {"error": f"Sidecar {resp.status_code} : {resp.text[:300]}"}
        data = resp.json()

    return {
        "columns": columns,
        "row_count": len(rows),
        "anonymized_csv": data.get("anonymized_text", csv_text),
        "mapping_count": len(data.get("mapping", {})),
        "anonymization_enabled": True,
    }


@mcp.tool()
async def set_anonymization(enabled: bool) -> dict:
    """Active ou désactive l'anonymisation pour la démo (toggle live, persistant).

    Crée ou supprime le fichier flag ~/.config/anon-gateway/disabled.
    Ignoré si la variable d'env ANON_DISABLED=1 est positionnée (override permanent).

    Args:
        enabled: True pour anonymiser (état normal), False pour bypass (PII visibles).
    """
    if ENV_DISABLED and enabled:
        return {
            "ok": False,
            "anonymization_enabled": False,
            "message": "Impossible d'activer : la variable d'env ANON_DISABLED=1 force le bypass. Retire-la et relance le MCP.",
        }

    FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        if FLAG_PATH.exists():
            FLAG_PATH.unlink()
        return {
            "ok": True,
            "anonymization_enabled": True,
            "message": f"Anonymisation ACTIVÉE. Flag supprimé : {FLAG_PATH}",
        }
    else:
        FLAG_PATH.touch()
        return {
            "ok": True,
            "anonymization_enabled": False,
            "message": f"Anonymisation DÉSACTIVÉE. Flag créé : {FLAG_PATH} — les requêtes query_db retourneront les PII en clair.",
        }


@mcp.tool()
async def status() -> dict:
    """Diagnostique la configuration du MCP server (utile en démo)."""
    db_ok = bool(DEMO_DB_PATH) and Path(DEMO_DB_PATH).exists()
    return {
        "sidecar_url": SIDECAR_URL,
        "demo_db_path": DEMO_DB_PATH or "(non configuré)",
        "demo_db_exists": db_ok,
        "anonymization_enabled": not _anonymization_disabled(),
        "env_disabled": ENV_DISABLED,
        "flag_path": str(FLAG_PATH),
        "flag_present": FLAG_PATH.exists(),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")

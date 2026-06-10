"""GET /api/kb/snapshot — snapshot complet de la KB consommé par les sidecars.

Le sidecar pull ça au démarrage + toutes les 600 s. La réponse contient :
  - `version`        : sha256(canonical_json) — sert d'ETag
  - `column_labels`  : entrées actives uniquement (status='active') — les
    'pending' ne sont JAMAIS distribuées, elles attendent validation admin
  - `custom_patterns`: tous les patterns actifs (regex Presidio)
  - `hook_enabled`, `deanon_enabled` : pour que le sidecar reflète les
    toggles côté serveur sans hard-coder

ETag : si le client envoie `If-None-Match: <sha256>` et que la version n'a
pas changé, on renvoie 304 Not Modified (zéro body, économise la bande).

Pas d'auth en phase 2 pour ce GET — c'est de la métadonnée publique de
schéma, pas de PII. (À revoir si plus tard on dépose des regex sensibles
dans `custom_patterns`.)
"""
from __future__ import annotations

import hashlib
import json
import datetime

from fastapi import APIRouter, Request, Response

from gateway.logging_config import get as _log

_logger = _log("gateway.kb_snapshot")
router = APIRouter(prefix="/api/kb", tags=["kb"])


def _canonical(payload: dict) -> str:
    """Stable JSON serialization for hashing — sorted keys, no whitespace."""

    def _default(obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        raise TypeError(f"Unhandled type {type(obj)!r}")

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default)


@router.get("/snapshot")
async def get_snapshot(request: Request, response: Response):
    store = request.app.state.column_labels
    cfg_store = request.app.state.config_store

    active_labels = await store.list_all(status="active")
    # Strip per-row timestamps that change without semantic change
    labels = [
        {
            "header_norm": r["header_norm"],
            "header_raw":  r["header_raw"],
            "label":       r["label"],
            "source":      r["source"],
            "confidence":  r["confidence"],
            # Phase 4.5 — contexte (db, table). NULL = règle générique.
            "db_name":     r.get("db_name"),
            "table_name":  r.get("table_name"),
        }
        for r in active_labels
    ]
    raw_patterns = await cfg_store.list_patterns()
    patterns = [
        {
            "name":         p["name"],
            "regex":        p["regex"],
            "entity_label": p["entity_label"],
            "score":        p["score"],
        }
        for p in raw_patterns
        if p["active"]
    ]
    cfg = await cfg_store.get_ner_config()

    payload = {
        "column_labels":   labels,
        "custom_patterns": patterns,
        "hook_enabled":    cfg["hook_enabled"],
        "deanon_enabled":  cfg["deanon_enabled"],
    }
    version = hashlib.sha256(_canonical(payload).encode()).hexdigest()

    # ETag round-trip
    if request.headers.get("if-none-match") == version:
        response.status_code = 304
        response.headers["ETag"] = version
        return Response(status_code=304, headers={"ETag": version})

    _logger.info(
        "kb_snapshot.served",
        extra={
            "version":         version[:12],
            "n_column_labels": len(labels),
            "n_patterns":      len(patterns),
        },
    )
    response.headers["ETag"] = version
    return {"version": version, **payload}

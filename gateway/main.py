# Copyright (c) 2026 David Miguel Loureiro Neri <david@neri.contact>
# Licensed under the PolyForm Noncommercial License 1.0.0.
# See LICENSE file for details.

"""Gateway zero-trust — métadonnées uniquement.

Toute la chaîne d'anonymisation (NER, mapping Redis, deanon) a migré dans
le sidecar local sur la machine de chaque utilisateur (cf. sidecar/). Le
gateway sert désormais uniquement à orchestrer la gouvernance partagée :

- KB de colonnes (column_labels) — métadonnées de schéma, pas de PII
- Patterns custom Presidio (regex) — métadonnées, pas de PII
- Clés API utilisateurs (hashées)
- Classifier qwen3-pii — appelé par les sidecars sur les colonnes ambiguës
  (cf. /api/classify_column, side-channel pragmatique assumé)

Aucun PII n'est jamais lu, stocké, ou logué côté gateway. Les seules
exceptions sont les sample values transmises dans le body de
POST /api/classify_column — qui ne sont jamais persistées ni loguées.
"""
from __future__ import annotations
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from slowapi.middleware import SlowAPIMiddleware

from gateway.admin_router import router as admin_router
from gateway.audit_router import router as audit_router
from gateway.classify_router import router as classify_router
from gateway.column_classifier import ColumnClassifier
from gateway.column_labels import ColumnLabelStore, seed_static_map
from gateway.config_store import ConfigStore
from gateway.kb_snapshot import router as kb_snapshot_router
from gateway.limiter import limiter
from gateway.logging_config import configure as _configure_logging, get as _log

_configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
_logger = _log("gateway.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await asyncpg.create_pool(os.environ["POSTGRES_DSN"])
    await seed_static_map(app.state.db_pool)
    # ColumnLabelStore in metadata-only mode (no Redis on the gateway).
    app.state.column_labels = ColumnLabelStore(app.state.db_pool, redis_client=None)
    app.state.config_store = ConfigStore(app.state.db_pool)
    # Standalone classifier for /api/classify_column — talks to Ollama
    app.state.classifier = ColumnClassifier(
        ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
    )

    cfg = await app.state.config_store.get_ner_config()
    _logger.info(
        "gateway.startup",
        extra={
            "mode": "metadata-only",
            "active_labels": cfg["active_labels"],
            "hook_enabled": cfg["hook_enabled"],
            "deanon_enabled": cfg["deanon_enabled"],
        },
    )
    yield
    await app.state.classifier.aclose()
    await app.state.db_pool.close()


app = FastAPI(title="LLM Anonymization Gateway (metadata-only)", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.include_router(admin_router)
app.include_router(audit_router)
app.include_router(classify_router)
app.include_router(kb_snapshot_router)

# Phase 4.6 — OAuth 2.0 client_credentials. Le sidecar (et autres M2M) obtient
# un JWT via POST /oauth/token et l'envoie en Bearer pour les calls
# /api/kb/snapshot et /api/classify_column. Coexiste avec les bearer keys
# legacy (table api_keys) — auth.py route automatiquement.
from gateway.oauth_router import router as oauth_router  # noqa: E402
app.include_router(oauth_router)

from gateway.dwh_router import router as dwh_router  # noqa: E402
app.include_router(dwh_router)

# Copyright (c) 2026 David Miguel Loureiro Neri <david@neri.contact>
# Licensed under the PolyForm Noncommercial License 1.0.0.
# See LICENSE file for details.

"""Sidecar local — détecteur NER + assignation de tokens + Redis local.

Phase 1 du refacto zero-trust : tourne sur 127.0.0.1, embarque la même pile
NER que le gateway legacy (GLiNER + Presidio + qwen3-pii via httpx), mais
les mappings token↔PII restent sur la machine de l'utilisateur.

Endpoints :
  POST /anonymize    body {"text": "..."}    → {"anonymized_text", "mapping"}
  POST /deanonymize  body {"text": "..."}    → {"result": "..."}
  GET  /mapping                              → {token: original, ...}
  DELETE /mapping                            → {"status": "cleared"}
  GET  /healthz                              → {"status": "ok"}

Auth : pas en phase 1 (loopback only). Token X-Sidecar-Token en phase 5.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import jwt
import redis.asyncio as redis
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from sidecar.anonymizer import Anonymizer
from sidecar.audit_client import AuditClient
from sidecar.cache import CacheService
from sidecar.dashboard_events import DashboardEventBus, event_to_sse
from sidecar.kb_client import KBClient
from sidecar.kb_label_store import KBSnapshotLabelStore
from sidecar.oauth_client import OAuthClient
from sidecar.logging_config import configure as _configure_logging, get as _log

_configure_logging(os.environ.get("ANON_SIDECAR_LOG_LEVEL", "INFO"))
from sidecar.ner import NERConfig
from sidecar.proxy import router as proxy_router
from sidecar.proxy_anonymizer import PayloadAnonymizer

_logger = _log("sidecar.main")

# Single fixed user_id for the sidecar — there's exactly one human per machine.
LOCAL_USER_ID = "local-user"


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_password = os.environ.get("REDIS_PASSWORD", "")
    if redis_password:
        app.state.redis = redis.Redis.from_url(redis_url, password=redis_password, decode_responses=False)
    else:
        app.state.redis = redis.Redis.from_url(redis_url, decode_responses=False)
    app.state.cache = CacheService(app.state.redis)
    app.state.events_bus = DashboardEventBus()
    # Gadget de démo : si True, le proxy laisse les jetons bruts dans la
    # réponse (masque MIS). Défaut False = deanon active = comportement prod.
    app.state.demo_mask_on = False

    # Phase 4.6 — OAuth M2M. Si OAUTH_TOKEN_URL/CLIENT_ID/CLIENT_SECRET sont
    # configurés, on s'authentifie auprès du gateway via JWT. Sinon, on retombe
    # sur GATEWAY_API_KEY (legacy).
    app.state.oauth_client = OAuthClient.from_env()
    if app.state.oauth_client:
        _logger.info("sidecar.oauth.enabled")

    app.state.kb_client = KBClient(
        gateway_url=os.environ.get("GATEWAY_URL", "http://localhost:8001"),
        api_key=os.environ.get("GATEWAY_API_KEY", ""),
        oauth_client=app.state.oauth_client,
    )
    app.state.audit_client = AuditClient(
        gateway_url=os.environ.get("GATEWAY_URL", "http://localhost:8001"),
        api_key=os.environ.get("GATEWAY_API_KEY", ""),
        oauth_client=app.state.oauth_client,
    )
    # Cold-start: try gateway, fall back to disk cache if unreachable
    pulled = await app.state.kb_client.pull()
    if not pulled and not app.state.kb_client.snapshot:
        app.state.kb_client.load_from_disk()
    await app.state.kb_client.start_background_refresh()

    # KB lookup câblé sur le snapshot — ce qui rend KB cache visible dans le
    # dashboard et évite des appels qwen redondants sur les headers connus.
    app.state.label_store = KBSnapshotLabelStore(app.state.kb_client)

    app.state.anonymizer = Anonymizer(
        ollama_url="",
        gliner_model=os.environ.get("GLINER_MODEL"),
        column_labels=app.state.label_store,
        events_bus=app.state.events_bus,
        oauth_client=app.state.oauth_client,
        audit_client=app.state.audit_client,
    )
    app.state.ner_config = NERConfig.default(app.state.anonymizer.ner)

    # Phase 2 du plan PROXY — anonymise le payload Anthropic Messages avant
    # forward. Réutilise le même Anonymizer et le même Redis que /anonymize,
    # donc les mappings sont cohérents entre l'API du sidecar et le proxy.
    app.state.payload_anonymizer = PayloadAnonymizer(
        anonymizer=app.state.anonymizer,
        cache=app.state.cache,
        user_id=LOCAL_USER_ID,
        ner_config=app.state.ner_config,
    )

    _logger.info("sidecar.startup", extra={"redis_url": redis_url})
    yield
    await app.state.kb_client.aclose()
    await app.state.audit_client.aclose()
    await app.state.redis.aclose()
    await app.state.anonymizer.aclose()
    if app.state.oauth_client is not None:
        await app.state.oauth_client.aclose()


app = FastAPI(lifespan=lifespan, title="Anon Sidecar", version="0.1.0")

# Proxy zero-trust Claude Code ↔ api.anthropic.com — Phase 1 (tunnel pur).
# Routes : POST /v1/messages, HEAD /. Coexistent avec les routes /anonymize
# etc. — pas de conflit de path.
app.include_router(proxy_router)

# CORS — autorise le frontend hébergé sur le LAN à appeler 127.0.0.1
_allowed_origin = os.environ.get("ANON_SIDECAR_ALLOWED_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Sidecar-Token"],
)


# Optional shared-secret auth — activé seulement si ANON_SIDECAR_TOKEN est défini.
# Permet à hooks/MCP de prouver qu'ils tournent dans la même session user, sans
# imposer la charge à un frontend hébergé sur le LAN qui ne peut pas lire ~/.config.
_AUTH_TOKEN = os.environ.get("ANON_SIDECAR_TOKEN", "")
OAUTH_ENABLED = os.environ.get("OAUTH_ENABLED", "").strip().lower() in ("1", "true", "yes")
OAUTH_SIGNING_KEY = os.environ.get("OAUTH_SIGNING_KEY", "")
OAUTH_ALGORITHM = "HS256"


def _validate_sidecar_jwt(token: str) -> bool:
    if not OAUTH_SIGNING_KEY:
        return False
    try:
        claims = jwt.decode(token, OAUTH_SIGNING_KEY, algorithms=[OAUTH_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False
    if claims.get("aud") != "anon-sidecar":
        return False
    return True


def _require_token(x_sidecar_token: str | None = Header(default=None)) -> None:
    if OAUTH_ENABLED:
        if not x_sidecar_token or not _validate_sidecar_jwt(x_sidecar_token):
            raise HTTPException(status_code=401, detail="invalid sidecar token")
    elif _AUTH_TOKEN:
        if x_sidecar_token != _AUTH_TOKEN:
            raise HTTPException(status_code=401, detail="invalid sidecar token")


class AnonContext(BaseModel):
    """Contexte optionnel pour le lookup hiérarchique KB (Phase 4.5).
    Quand le caller connaît la table source (ex: MCP query_db qui parse FROM),
    il l'envoie ici. Sinon laissé None → fallback générique tous-contextes."""
    db: str | None = None
    table: str | None = None


class TextIn(BaseModel):
    text: str
    context: AnonContext | None = None


@app.get("/healthz")
async def healthz():
    # healthz must work without auth so install scripts and hooks can probe
    return {"status": "ok"}


@app.post("/anonymize", dependencies=[Depends(_require_token)])
async def anonymize(request: Request, body: TextIn):
    ctx = body.context.model_dump() if body.context else None
    try:
        anon_text, mapping = await request.app.state.anonymizer.anonymize(
            body.text, request.app.state.cache, LOCAL_USER_ID, request.app.state.ner_config,
            context=ctx,
        )
    except Exception as exc:
        _logger.exception("anonymize.failed", extra={"err": str(exc)})
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"error": {"type": "anonymization_error", "message": "Anonymization failed — request blocked (fail-safe)"}},
        )
    return {"anonymized_text": anon_text, "mapping": mapping}


@app.post("/deanonymize", dependencies=[Depends(_require_token)])
async def deanonymize(request: Request, body: TextIn):
    mapping = await request.app.state.cache.get_mapping(LOCAL_USER_ID)
    result = request.app.state.anonymizer.deanonymize(body.text, mapping)
    return {"result": result}


@app.get("/mapping", dependencies=[Depends(_require_token)])
async def get_mapping(request: Request):
    return await request.app.state.cache.get_mapping(LOCAL_USER_ID)


@app.delete("/mapping", dependencies=[Depends(_require_token)])
async def clear_mapping(request: Request):
    await request.app.state.cache.clear_mapping(LOCAL_USER_ID)
    # Invalider la hash cache in-memory du proxy_anonymizer — sinon elle
    # référence des tokens qui n'existent plus en Redis et le proxy ne
    # peut plus désanonymiser ces tokens en retour.
    pa = getattr(request.app.state, "payload_anonymizer", None)
    if pa is not None:
        pa.reset_hash_cache()
    return {"status": "cleared"}


@app.post("/refresh", dependencies=[Depends(_require_token)])
async def refresh_kb(request: Request):
    updated = await request.app.state.kb_client.pull()
    return {
        "updated": updated,
        "version": request.app.state.kb_client.version,
    }


class MaskIn(BaseModel):
    on: bool


# Gadget de démo (pas de prod) : bascule l'affichage des réponses entre
# vrais noms (masque levé, deanon active) et jetons (masque mis). Sans token
# pour que la page bouton hébergée sur le LAN puisse l'appeler, comme /healthz.
@app.get("/demo/mask")
async def get_demo_mask(request: Request):
    return {"on": getattr(request.app.state, "demo_mask_on", False)}


@app.post("/demo/mask")
async def set_demo_mask(request: Request, body: MaskIn):
    request.app.state.demo_mask_on = body.on
    return {"on": body.on}


# ─── Dashboard de démo ────────────────────────────────────────────────
# Ces endpoints servent le dashboard visuel des détections. Auth volontairement
# absente : le dashboard est un frontend localhost, et exposer les PII détectées
# en plus serait redondant (elles sont déjà côté user dans le mapping Redis).
# CORS gère l'allowed-origin pour éviter qu'un site distant aspire les events.

@app.get("/events/recent")
async def events_recent(request: Request, limit: int = 50):
    return {"events": request.app.state.events_bus.recent(limit=limit)}


@app.get("/events")
async def events_sse(request: Request):
    bus: DashboardEventBus = request.app.state.events_bus

    async def stream():
        # Keep-alive ping toutes les 15s pour éviter le timeout proxy/navigateur
        try:
            async for ev in bus.subscribe():
                yield event_to_sse(ev)
        except Exception:
            return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

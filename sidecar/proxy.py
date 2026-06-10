# Copyright (c) 2026 David Miguel Loureiro Neri <david@neri.contact>
# Licensed under the PolyForm Noncommercial License 1.0.0.
# See LICENSE file for details.

"""Proxy zero-trust transparent Claude Code ↔ api.anthropic.com.

Phase 1 : tunnel pur (routing + streaming validés).
Phase 2 : anonymisation outbound du payload Messages avant forward.
Phase 3 : désanonymisation inbound streaming avec buffer rolling pour
gérer les placeholders à cheval entre chunks SSE.

Endpoints exposés :
- POST /v1/messages — forward vers api.anthropic.com/v1/messages
- HEAD /            — réponse 200 directe (Bun runtime de Claude Code envoie
                      un health check au démarrage, on évite le bruit log
                      Anthropic 404)

Pour activer côté Claude Code :
    ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
"""
from __future__ import annotations

import json
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from sidecar.logging_config import get as _log
from sidecar.proxy_anonymizer import UnsupportedBlockError
from sidecar.proxy_deanonymizer import StreamDeanonymizer

_logger = _log("sidecar.proxy")

UPSTREAM = "https://api.anthropic.com"

# Headers calculés par httpx ou strict-hop-by-hop : à ne pas re-forwarder.
HOP_BY_HOP = {
    "host", "content-length", "connection", "transfer-encoding",
    "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "upgrade",
    # Accept-Encoding : on demande à Anthropic de répondre en clair (pas
    # gzip) pour pouvoir manipuler le texte SSE en Phase 3 (désanonymisation
    # streaming). Coût négligeable côté bande passante interne.
    "accept-encoding",
}

router = APIRouter(tags=["proxy"])


@router.head("/")
async def health_head():
    """Bun (runtime de Claude Code) envoie HEAD / au boot. Anthropic répond
    404 — on retourne 200 ici pour éviter du bruit dans les logs."""
    return Response(status_code=200)


@router.post("/v1/messages")
async def proxy_messages(request: Request):
    body = await request.body()

    # ── Phase 2 : anonymisation outbound ──────────────────────────────────
    # On ré-écrit le body avant le forward. PayloadAnonymizer parse le JSON
    # Anthropic Messages API et anonymise system, messages (texte + tool_use
    # + tool_result), avec un cache par hash pour éviter de re-NER-iser le
    # contexte qui se répète à chaque tour.
    payload_anonymizer = getattr(request.app.state, "payload_anonymizer", None)
    if payload_anonymizer is not None:
        try:
            anon_started = time.time()
            body = await payload_anonymizer.anonymize_body(body)
            anon_ms = int((time.time() - anon_started) * 1000)
            _logger.info("proxy.anonymized", extra={
                "out_bytes": len(body), "elapsed_ms": anon_ms,
            })
        except UnsupportedBlockError as e:
            # Fail-closed : bloc binaire (image/document) non anonymisable.
            _logger.warning("proxy.unsupported_block", extra={"block_type": e.block_type})
            return Response(
                content=json.dumps({
                    "error": {
                        "type": "invalid_request_error",
                        "message": (
                            f"Bloc '{e.block_type}' non supporté par le proxy "
                            "d'anonymisation (contenu binaire, pas d'OCR local) — "
                            "requête bloquée (fail-closed)."
                        ),
                    }
                }).encode(),
                status_code=400,
                media_type="application/json",
            )
        except Exception as e:
            # Fail-safe : si l'anonymisation crashe, on REFUSE la requête
            # plutôt que de laisser passer les PII en clair.
            _logger.exception("proxy.anonymize_failed", extra={"err": str(e)})
            return Response(
                content=json.dumps({
                    "error": {
                        "type": "anonymization_error",
                        "message": "Anonymization failed — request blocked (fail-safe)",
                    }
                }).encode(),
                status_code=503,
                media_type="application/json",
            )

    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP
    }
    # Forcer Anthropic à répondre en clair (pas gzip/br) : httpx ajoute par
    # défaut "gzip, deflate, br" si le header est absent, donc on l'override
    # explicitement plutôt que de juste le stripper.
    fwd_headers["accept-encoding"] = "identity"

    query = request.url.query
    upstream_url = f"{UPSTREAM}/v1/messages"
    if query:
        upstream_url += f"?{query}"

    started = time.time()
    client = httpx.AsyncClient(timeout=120.0)
    upstream_req = client.build_request(
        method="POST",
        url=upstream_url,
        headers=fwd_headers,
        content=body,
    )

    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        _logger.warning("proxy.upstream_error", extra={"err": str(e)})
        return Response(
            content=json.dumps({"error": str(e)}).encode(),
            status_code=502,
            media_type="application/json",
        )

    elapsed_ms = int((time.time() - started) * 1000)
    _logger.info("proxy.request", extra={
        "status": upstream_resp.status_code,
        "ct": upstream_resp.headers.get("content-type", "")[:40],
        "elapsed_ms": elapsed_ms,
        "body_bytes": len(body),
        "query": query or None,
    })

    # ── Phase 3 : désanonymisation inbound streaming ──────────────────────
    # Snapshot initial du mapping (token → valeur) depuis Redis local. Un
    # cache_lookup closure résout à la volée les tokens créés *pendant* le
    # stream (par ex. nouveaux MCP tool calls) — ferme la race condition du
    # snapshot figé documentée dans le checkpoint 2026-05-25.
    deanon: StreamDeanonymizer | None = None
    cache = getattr(request.app.state, "cache", None)
    LOCAL_USER_ID = "local-user"  # même valeur que dans main.py
    # Gadget de démo : masque MIS → on saute la deanon, les jetons passent
    # bruts jusqu'au terminal Claude Code (voir POST /demo/mask).
    demo_mask_on = getattr(request.app.state, "demo_mask_on", False)
    if cache is not None and upstream_resp.status_code == 200 and not demo_mask_on:
        try:
            mapping = await cache.get_mapping(LOCAL_USER_ID)

            async def _lookup(token: str):
                raw = await cache.redis.hget(f"mapping:{LOCAL_USER_ID}", token)
                return raw.decode() if raw else None

            # On instancie même si snapshot vide : le lookup peut tout résoudre.
            deanon = StreamDeanonymizer(mapping or {}, cache_lookup=_lookup)
        except Exception as e:
            _logger.warning("proxy.deanon_init_failed", extra={"err": str(e)})

    async def stream():
        chunks = 0
        total = 0
        try:
            async for chunk in upstream_resp.aiter_raw():
                chunks += 1
                total += len(chunk)
                if deanon is None:
                    yield chunk
                    continue
                # Parsing SSE-structuré : extrait `delta.text` des text_delta
                # et applique deanon dessus, puis ré-émet l'event. Bridge les
                # placeholders splittés entre 2 events (`[` dans l'un,
                # `PERSONNE_X]` dans le suivant).
                out = await deanon.feed_sse(chunk)
                if out:
                    yield out
            # Fin du stream : process le résidu + flush le buffer logique.
            if deanon is not None:
                final = await deanon.flush_final_sse()
                if final:
                    yield final
        finally:
            await upstream_resp.aclose()
            await client.aclose()
            _logger.info("proxy.stream_done", extra={
                "chunks": chunks, "total_bytes": total,
                "deanon": deanon is not None,
            })

    resp_headers = {
        k: v for k, v in upstream_resp.headers.items()
        if k.lower() not in HOP_BY_HOP
    }

    return StreamingResponse(
        content=stream(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=upstream_resp.headers.get("content-type"),
    )

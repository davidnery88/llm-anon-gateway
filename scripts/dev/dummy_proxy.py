"""Proxy forward-only de capture — Phase 0 du plan PROXY.

Écoute sur 127.0.0.1:8788. Tout ce qui arrive est loggué (méthode, chemin,
headers significatifs, taille du body) puis forwardé verbatim à
https://api.anthropic.com. La réponse est streamée chunk par chunk vers
l'appelant (Claude Code).

Ne modifie rien — c'est un mode tunnel pur, juste pour vérifier que
ANTHROPIC_BASE_URL est respecté et identifier les endpoints touchés.

Lancement :
    mcp_server/venv/bin/python scripts/dev/dummy_proxy.py

Puis dans un autre terminal :
    ANTHROPIC_BASE_URL=http://127.0.0.1:8788 claude

Les logs sortent en JSON sur stdout, un événement par ligne.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

UPSTREAM = "https://api.anthropic.com"
PORT = 8788

# Headers qu'on ne forwarde pas (calculés par httpx ou ajoutés par hop-by-hop)
HOP_BY_HOP = {
    "host", "content-length", "connection", "transfer-encoding",
    "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "upgrade",
}


def log(event: str, **kwargs) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **kwargs,
    }
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def _interesting_headers(headers) -> dict:
    """Headers utiles à logguer pour comprendre ce qui se passe."""
    keys = {"authorization", "anthropic-version", "anthropic-beta",
            "user-agent", "content-type", "accept", "x-api-key"}
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in keys:
            # Masquer le contenu des tokens, garder juste la signature
            if kl in ("authorization", "x-api-key"):
                if v.lower().startswith("bearer "):
                    out[k] = f"Bearer ...{v[-8:]}"
                else:
                    out[k] = f"...{v[-8:]}" if len(v) > 8 else "<short>"
            else:
                out[k] = v
    return out


async def proxy(request: Request) -> StreamingResponse:
    req_id = f"{int(time.time() * 1000)}-{id(request) & 0xFFFF:04x}"
    path = request.url.path
    query = request.url.query
    upstream_url = f"{UPSTREAM}{path}"
    if query:
        upstream_url += f"?{query}"

    body = await request.body()

    log(
        "request.in",
        req_id=req_id,
        method=request.method,
        path=path,
        query=query or None,
        body_bytes=len(body),
        headers=_interesting_headers(request.headers),
    )

    # Headers à forwarder
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP
    }

    # HTTP/1.1 — Anthropic supporte les deux et h2 n'est pas installé dans le venv MCP
    client = httpx.AsyncClient(timeout=120.0)
    started = time.time()

    upstream_req = client.build_request(
        method=request.method,
        url=upstream_url,
        headers=fwd_headers,
        content=body,
    )

    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as e:
        log("request.fail", req_id=req_id, error=str(e))
        await client.aclose()
        return StreamingResponse(
            content=iter([json.dumps({"error": str(e)}).encode()]),
            status_code=502,
            media_type="application/json",
        )

    elapsed_ms = int((time.time() - started) * 1000)
    log(
        "response.start",
        req_id=req_id,
        status=upstream_resp.status_code,
        upstream_url=upstream_url,
        content_type=upstream_resp.headers.get("content-type"),
        elapsed_ms=elapsed_ms,
    )

    # Re-streamer la réponse
    async def stream():
        total = 0
        chunks = 0
        try:
            async for chunk in upstream_resp.aiter_raw():
                total += len(chunk)
                chunks += 1
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()
            log(
                "response.done",
                req_id=req_id,
                total_bytes=total,
                chunks=chunks,
            )

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


app = Starlette(routes=[
    Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]),
])


if __name__ == "__main__":
    log("proxy.start", port=PORT, upstream=UPSTREAM)
    try:
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    except KeyboardInterrupt:
        log("proxy.stop", reason="keyboard-interrupt")
        sys.exit(0)

from __future__ import annotations
import asyncio
import json
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from gateway.dwh_sources import DwhSourceStore, decrypt_secret
from gateway.db_connectors import get_connector
from gateway.scanner import run_scan

router = APIRouter(prefix="/admin", tags=["dwh"])


def _require_admin(request: Request):
    secret = request.headers.get("X-Admin-Secret")
    if not secret or secret != os.environ.get("ADMIN_SECRET"):
        raise HTTPException(status_code=403, detail="Admin access denied")


class SourceIn(BaseModel):
    name: str
    db_type: str
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    options: dict = {}
    db_filter: list[str] = []


class ScanIn(BaseModel):
    dbs: list[str] = []
    sample_n: int = Field(default=10, ge=1, le=1000)


def _store(request: Request) -> DwhSourceStore:
    return DwhSourceStore(request.app.state.db_pool)


def _with_password(src: dict) -> dict:
    """Déchiffre le password et l'injecte sous _password (jamais persisté/loggé)."""
    out = dict(src)
    if isinstance(out.get("options"), str):
        out["options"] = json.loads(out["options"])
    enc = out.get("password_encrypted")
    out["_password"] = decrypt_secret(enc) if enc else None
    return out


@router.get("/dwh_sources", dependencies=[Depends(_require_admin)])
async def list_sources(request: Request):
    return await _store(request).list()


@router.post("/dwh_sources", dependencies=[Depends(_require_admin)])
async def create_source(request: Request, body: SourceIn):
    return await _store(request).create(**body.model_dump())


@router.put("/dwh_sources/{sid}", dependencies=[Depends(_require_admin)])
async def update_source(request: Request, sid: int, body: SourceIn):
    return await _store(request).update(sid, **body.model_dump())


@router.delete("/dwh_sources/{sid}", dependencies=[Depends(_require_admin)])
async def delete_source(request: Request, sid: int):
    await _store(request).delete(sid)
    return {"ok": True}


@router.post("/dwh_sources/{sid}/test", dependencies=[Depends(_require_admin)])
async def test_source(request: Request, sid: int):
    src = await _store(request).get(sid)
    if not src:
        raise HTTPException(404, "source inconnue")
    src = _with_password(src)
    try:
        dbs = await asyncio.to_thread(get_connector(src).list_databases)
        return {"ok": True, "databases": dbs}
    except Exception as e:
        raise HTTPException(400, f"connexion échouée: {e}")


@router.post("/dwh_sources/{sid}/scan", dependencies=[Depends(_require_admin)])
async def start_scan(request: Request, sid: int, body: ScanIn):
    store = _store(request)
    src = await store.get(sid)
    if not src:
        raise HTTPException(404, "source inconnue")
    src = _with_password(src)
    job = await store.create_job(sid)
    connector = get_connector(src)
    classifier = request.app.state.classifier
    labels = request.app.state.column_labels
    cfg = await request.app.state.config_store.get_ner_config()
    # get_ner_config() renvoie un dict (cf. gateway/config_store.py)
    threshold = (cfg or {}).get("qwen_auto_approve_threshold", 0.7)
    include_views = bool((src.get("options") or {}).get("include_views", True))
    dbs = body.dbs or list(src.get("db_filter") or []) or await asyncio.to_thread(connector.list_databases)

    async def _bg():
        await run_scan(connector=connector, classifier=classifier, labels=labels, jobs=store,
                       job_id=job["id"], dbs=dbs, include_views=include_views, threshold=threshold,
                       sample_n=body.sample_n, config_store=request.app.state.config_store)
        st = await store.get_job(job["id"])
        await store.set_last_scan(sid, st["status"])

    asyncio.create_task(_bg())
    return {"job_id": job["id"]}


@router.get("/dwh_sources/scan/{job_id}", dependencies=[Depends(_require_admin)])
async def scan_status(request: Request, job_id: int):
    job = await _store(request).get_job(job_id)
    if not job:
        raise HTTPException(404, "job inconnu")
    return job


@router.post("/dwh_sources/scan/{job_id}/cancel", dependencies=[Depends(_require_admin)])
async def scan_cancel(request: Request, job_id: int):
    await _store(request).update_job(job_id, status="cancelled")
    return {"ok": True}

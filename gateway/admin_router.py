from __future__ import annotations
import os
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from gateway.config_store import ConfigStore
from gateway.logging_config import get as _log
from gateway.regex_guard import validate_regex

_logger = _log("gateway.admin")


def _require_admin(request: Request) -> None:
    secret = os.environ.get("ADMIN_SECRET", "")
    if not secret or request.headers.get("X-Admin-Secret") != secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access denied")


router = APIRouter(prefix="/admin", dependencies=[Depends(_require_admin)])


def _store(request: Request) -> ConfigStore:
    return ConfigStore(request.app.state.db_pool)


async def _rebuild_ner_config(request: Request) -> None:
    """No-op on the metadata-only gateway.

    NER config used to be rebuilt in-memory here so the (now-deleted)
    gateway anonymizer could pick up admin edits live. In the zero-trust
    model, the NER lives in each user's sidecar — admin edits propagate
    through GET /api/kb/snapshot pulled by sidecars every 600 s (or via
    POST /refresh on a specific sidecar).
    """
    return None


class ConfigUpdate(BaseModel):
    active_labels: list[str]
    gliner_threshold: float
    gliner_enabled: bool = True
    presidio_enabled: bool = True
    classifier_enabled: bool = True
    deanon_enabled: bool = True
    hook_enabled: bool = True
    qwen_auto_approve_threshold: float = 0.7


class PatternCreate(BaseModel):
    name: str
    regex: str
    entity_label: str
    score: float = 0.8


class KeyCreate(BaseModel):
    label: str


class HeaderLabelCreate(BaseModel):
    header: str
    label: str


class HeaderLabelUpdate(BaseModel):
    label: str


class BulkApproveRequest(BaseModel):
    label_ids: list[int]


@router.get("/config")
async def get_config(request: Request):
    store = _store(request)
    cfg = await store.get_ner_config()
    patterns = await store.list_patterns()
    return {**cfg, "patterns": patterns}


@router.put("/config")
async def update_config(request: Request, body: ConfigUpdate):
    store = _store(request)
    await store.update_ner_config(
        body.active_labels, body.gliner_threshold,
        body.gliner_enabled, body.presidio_enabled, body.classifier_enabled,
        body.deanon_enabled, body.hook_enabled,
        body.qwen_auto_approve_threshold,
    )
    await _rebuild_ner_config(request)
    _logger.info("admin.config.updated", extra={
        "active_labels": body.active_labels, "threshold": body.gliner_threshold,
        "gliner_enabled": body.gliner_enabled, "presidio_enabled": body.presidio_enabled,
        "classifier_enabled": body.classifier_enabled,
        "deanon_enabled": body.deanon_enabled, "hook_enabled": body.hook_enabled,
        "qwen_auto_approve_threshold": body.qwen_auto_approve_threshold,
    })
    return {"status": "updated"}


@router.get("/patterns")
async def list_patterns(request: Request):
    return await _store(request).list_patterns()


@router.post("/patterns")
async def create_pattern(request: Request, body: PatternCreate):
    store = _store(request)
    ok, reason = validate_regex(body.regex)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Regex refusé : {reason}")
    try:
        result = await store.create_pattern(body.name, body.regex, body.entity_label, body.score)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"Un pattern nommé '{body.name}' existe déjà.")
    # Auto-add the new entity label to active_labels if not already present
    raw = await store.get_ner_config()
    if body.entity_label not in raw["active_labels"]:
        updated_labels = raw["active_labels"] + [body.entity_label]
        await store.update_ner_config(
            updated_labels, raw["gliner_threshold"],
            raw["gliner_enabled"], raw["presidio_enabled"], raw["classifier_enabled"],
            raw["deanon_enabled"], raw["hook_enabled"],
            raw["qwen_auto_approve_threshold"],
        )
    await _rebuild_ner_config(request)
    _logger.info("admin.pattern.created", extra={"pattern": body.name, "entity_label": body.entity_label})
    return result


@router.delete("/patterns/{pattern_id}")
async def delete_pattern(request: Request, pattern_id: int):
    await _store(request).delete_pattern(pattern_id)
    await _rebuild_ner_config(request)
    _logger.info("admin.pattern.deleted", extra={"pattern_id": pattern_id})
    return {"status": "deleted"}


@router.get("/keys")
async def list_keys(request: Request):
    return await _store(request).list_api_keys()


@router.post("/keys")
async def create_key(request: Request, body: KeyCreate):
    result = await _store(request).create_api_key(body.label)
    _logger.info("admin.key.created", extra={"label": body.label, "key_id": result.get("id")})
    return result


@router.delete("/keys/{key_id}")
async def revoke_key(request: Request, key_id: int):
    await _store(request).revoke_api_key(key_id)
    _logger.info("admin.key.revoked", extra={"key_id": key_id})
    return {"status": "revoked"}


@router.get("/column_labels")
async def list_column_labels(request: Request, status: str | None = None):
    return await request.app.state.column_labels.list_all(status=status)


@router.post("/column_labels")
async def create_column_label(request: Request, body: HeaderLabelCreate):
    result = await request.app.state.column_labels.upsert(
        body.header, body.label, source="admin", status="active"
    )
    _logger.info("admin.column_label.created", extra={
        "header": body.header, "label": body.label, "label_id": result.get("id"),
    })
    return result


@router.put("/column_labels/{label_id}")
async def update_column_label(request: Request, label_id: int, body: HeaderLabelUpdate):
    result = await request.app.state.column_labels.update(label_id, body.label, source="admin")
    _logger.info("admin.column_label.updated", extra={
        "label_id": label_id, "label": body.label,
    })
    return result


@router.post("/column_labels/{label_id}/approve")
async def approve_column_label(request: Request, label_id: int):
    result = await request.app.state.column_labels.approve(label_id)
    _logger.info("admin.column_label.approved", extra={"label_id": label_id})
    return result


@router.post("/column_labels/{label_id}/reject")
async def reject_column_label(request: Request, label_id: int):
    await request.app.state.column_labels.reject(label_id)
    _logger.info("admin.column_label.rejected", extra={"label_id": label_id})
    return {"status": "rejected"}


@router.delete("/column_labels/{label_id}")
async def delete_column_label(request: Request, label_id: int):
    await request.app.state.column_labels.delete(label_id)
    _logger.info("admin.column_label.deleted", extra={"label_id": label_id})
    return {"status": "deleted"}


@router.post("/column_labels/bulk_approve")
async def bulk_approve_column_labels(request: Request, body: BulkApproveRequest):
    count = await request.app.state.column_labels.bulk_approve(body.label_ids)
    _logger.info("admin.column_label.bulk_approved", extra={
        "count": count, "label_ids": body.label_ids,
    })
    return {"approved": count}

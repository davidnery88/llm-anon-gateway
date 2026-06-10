from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.auth import validate_api_key
from gateway.limiter import limiter
from gateway.logging_config import get as _log

_logger = _log("gateway.audit")
router = APIRouter(prefix="/api", tags=["audit"])


class AuditEvent(BaseModel):
    user_id_hash: str = Field(min_length=64, max_length=64)
    text_hash: str = Field(min_length=64, max_length=64)
    entity_counts: dict = Field(default_factory=dict)
    sources: dict = Field(default_factory=dict)
    latency_ms: int = Field(ge=0)
    format: Optional[str] = Field(default=None, max_length=16)
    field_count: Optional[int] = Field(default=None, ge=0)
    token_count: int = Field(ge=0, default=0)


@router.post("/audit", status_code=204)
@limiter.limit("120/minute")
async def receive_audit(
    request: Request,
    body: AuditEvent = Body(...),
    user_id: str = Depends(validate_api_key),
):
    pool = request.app.state.db_pool
    try:
        await pool.execute(
            """INSERT INTO audit_log
               (user_id_hash, text_hash, entity_counts, sources, latency_ms, format, field_count, token_count)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            body.user_id_hash,
            body.text_hash,
            body.entity_counts,
            body.sources,
            body.latency_ms,
            body.format,
            body.field_count,
            body.token_count,
        )
    except Exception as exc:
        _logger.warning("audit.insert_failed", extra={"err": str(exc)})
        raise HTTPException(status_code=500, detail="Failed to store audit event")

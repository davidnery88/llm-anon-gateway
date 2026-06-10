"""POST /api/classify_column — qwen3-pii LLM classifier exposé au sidecar.

Le sidecar appelle ce endpoint UNIQUEMENT quand son NER local (GLiNER +
Presidio) n'arrive pas à classifier les valeurs d'une colonne inconnue.
C'est le "side-channel PII" assumé du modèle zero-trust : dans ~5% des
cas, des valeurs réelles transitent par le serveur le temps de la
requête. Mitigations :
  - Cap dur ≤5 valeurs
  - Pas de log du body (juste métadonnées : nom de colonne, label retourné,
    confidence, latency)
  - Rate limit 30/min par clé API (visible dans l'admin UI plus tard)
"""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.auth import validate_api_key
from gateway.limiter import limiter
from gateway.logging_config import get as _log

_logger = _log("gateway.classify")
router = APIRouter(prefix="/api", tags=["classify"])

MAX_VALUES = 5


class ValueMetadataEntry(BaseModel):
    length: int
    charset: str = Field(max_length=16)
    has_spaces: bool
    has_punctuation: bool
    regex_hint: str = Field(max_length=32)
    sample_hash: str = Field(max_length=8)


class ClassifyRequest(BaseModel):
    column: str = Field(min_length=1, max_length=128)
    sql_type: str = Field(default="varchar", max_length=32)
    values: Optional[list[str]] = Field(default=None, min_length=1)
    value_metadata: Optional[list[ValueMetadataEntry]] = Field(default=None)
    table: str = Field(default="unknown", max_length=128)


class ClassifyResponse(BaseModel):
    label: Optional[str]
    confidence: float


@router.post("/classify_column", response_model=ClassifyResponse)
@limiter.limit("30/minute")
async def classify_column(
    request: Request,
    body: ClassifyRequest = Body(...),
    user_id: str = Depends(validate_api_key),
):
    if body.value_metadata is not None:
        if len(body.value_metadata) > MAX_VALUES:
            raise HTTPException(
                status_code=400,
                detail=f"Max {MAX_VALUES} metadata entries per request (got {len(body.value_metadata)})",
            )
        meta = [m.model_dump() for m in body.value_metadata]
        values = None
    elif body.values is not None:
        if len(body.values) > MAX_VALUES:
            raise HTTPException(
                status_code=400,
                detail=f"Max {MAX_VALUES} sample values per request (got {len(body.values)})",
            )
        meta = None
        values = body.values
    else:
        raise HTTPException(status_code=400, detail="Either values or value_metadata must be provided")

    classifier = request.app.state.classifier
    label, confidence, _suggested_regex = await classifier.classify(
        table=body.table,
        column=body.column,
        sql_type=body.sql_type,
        values=values,
        value_metadata=meta,
    )
    # Deliberately do NOT log the values — only metadata.
    _logger.info(
        "classify.served",
        extra={
            "user": user_id[:8],
            "column": body.column,
            "sql_type": body.sql_type,
            "n_values": len(values) if values else len(meta) if meta else 0,
            "label": label,
            "confidence": confidence,
        },
    )
    return ClassifyResponse(label=label, confidence=confidence)

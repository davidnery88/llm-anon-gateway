from __future__ import annotations
import json
import re
import httpx
from gateway.logging_config import get as _log

_logger = _log("gateway.classifier")

SYSTEM_PROMPT = (
    "You are a PII column classifier. "
    "Given a database table name, column name, SQL type, and value metadata, "
    "return the single anonymization strategy that applies to this column. "
    "Value metadata describes sample values WITHOUT revealing the actual content. "
    "Each metadata entry contains: length (character count), charset (digits/alpha/alphanum/mixed/email/url), "
    "has_spaces (boolean), has_punctuation (boolean), regex_hint (matched pattern like iban_ch/avs/phone/email/date/none), "
    "and sample_hash (first 8 chars of SHA-256, for debugging only). "
    "Use the patterns in the metadata to infer the data type. For example: "
    "13 digits with regex_hint=avs indicates a Swiss AVS number; "
    "length ~35 with charset=alphanum and regex_hint=iban_ch indicates a Swiss IBAN; "
    "charset=email with regex_hint=email indicates an email address; "
    "charset=digits with regex_hint=phone indicates a phone number; "
    "charset=mixed with has_spaces=true and has_punctuation=true may indicate a name or address. "
    "Reply with ONLY a JSON object (no prose, no markdown fences) of the form: "
    '{"strategy": "<name>", "confidence": <0.0-1.0>, "regex": "<pattern or null>"}. '
    "The strategy must be exactly one of: mask_name, mask_email, mask_phone, "
    "mask_date, mask_address, mask_plate, mask_id, keep, redact. "
    "Confidence is your subjective certainty between 0.0 and 1.0. "
    "The regex field: if strategy is mask_id and the values follow a consistent structured format "
    "(e.g. a policy number, contract ID, employee ID), provide a Python regex that captures this format "
    "with word boundaries (\\\\b). If no consistent pattern is detectable, set regex to null."
)

STRATEGY_TO_LABEL: dict[str, str | None] = {
    "mask_name": "PERSONNE",
    "mask_email": "EMAIL",
    "mask_phone": "TEL",
    "mask_date": "DATE",
    "mask_address": "LOCALISATION",
    "mask_plate": "PLAQUE",
    "mask_id": "ID",
    "redact": "REDACT",
    "keep": None,
}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_STRATEGY_RE = re.compile(
    r"(mask_name|mask_email|mask_phone|mask_date|mask_address|mask_plate|mask_id|redact|keep)",
    re.IGNORECASE,
)


def _parse_response(raw: str) -> tuple[str, float, str | None]:
    """Parse classifier raw response into (strategy, confidence, regex).

    Tolerant of markdown fences, surrounding prose, and missing fields.
    Falls back to strategy keyword extraction with confidence=0.5 and regex=None.
    """
    if not raw:
        return "keep", 0.5, None

    cleaned = _FENCE_RE.sub("", raw).strip()

    def _extract(obj: dict) -> tuple[str, float, str | None]:
        strategy = str(obj.get("strategy", "keep")).strip().lower()
        try:
            confidence = float(obj.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        if strategy not in STRATEGY_TO_LABEL:
            m = _STRATEGY_RE.search(strategy)
            strategy = m.group(1).lower() if m else "keep"
        pattern = obj.get("regex") or None
        if pattern is not None:
            pattern = str(pattern).strip() or None
        return strategy, max(0.0, min(1.0, confidence)), pattern

    # try direct JSON
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return _extract(obj)
    except (json.JSONDecodeError, ValueError):
        pass

    # try embedded JSON object
    brace = cleaned.find("{")
    if brace != -1:
        end = cleaned.rfind("}")
        if end > brace:
            try:
                obj = json.loads(cleaned[brace : end + 1])
                if isinstance(obj, dict):
                    return _extract(obj)
            except (json.JSONDecodeError, ValueError):
                pass

    # last resort: strategy keyword only
    m = _STRATEGY_RE.search(cleaned)
    if m:
        return m.group(1).lower(), 0.5, None
    return "keep", 0.5, None


class ColumnClassifier:
    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.client = httpx.AsyncClient(base_url=ollama_url, timeout=10)

    async def classify(
        self, table: str, column: str, sql_type: str, values: list[str] | None = None, value_metadata: list[dict] | None = None,
    ) -> tuple[str | None, float, str | None]:
        if value_metadata is not None:
            user_msg = (
                f"Table: {table}\nColumn: {column}\n"
                f"SQL type: {sql_type}\nValue metadata: {json.dumps(value_metadata, ensure_ascii=False)}"
            )
        elif values is not None:
            user_msg = (
                f"Table: {table}\nColumn: {column}\n"
                f"SQL type: {sql_type}\nValues: {json.dumps(values, ensure_ascii=False)}"
            )
        else:
            _logger.warning("classifier.no_input", extra={"table": table, "column": column})
            return None, 0.0, None
        try:
            resp = await self.client.post("/api/generate", json={
                "model": "qwen3-pii",
                "system": SYSTEM_PROMPT,
                "prompt": user_msg,
                "stream": False,
            })
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
        except httpx.TimeoutException:
            _logger.warning("classifier.timeout", extra={"table": table, "column": column})
            return None, 0.0, None
        except httpx.HTTPError as exc:
            _logger.warning("classifier.http_error", extra={"table": table, "column": column, "error": str(exc)})
            return None, 0.0, None

        strategy, confidence, suggested_regex = _parse_response(raw)
        label = STRATEGY_TO_LABEL.get(strategy)
        _logger.info(
            "classifier.result",
            extra={
                "table": table,
                "column": column,
                "strategy": strategy,
                "label": label,
                "confidence": confidence,
                "regex": suggested_regex,
            },
        )
        return label, confidence, suggested_regex

    async def aclose(self) -> None:
        await self.client.aclose()

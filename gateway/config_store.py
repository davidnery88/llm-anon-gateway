from __future__ import annotations
import hashlib
import secrets

from gateway.logging_config import get as _log
from gateway.regex_guard import validate_regex

_logger = _log("gateway.config_store")


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ConfigStore:
    def __init__(self, pool):
        self._pool = pool

    async def get_ner_config(self) -> dict:
        row = await self._pool.fetchrow(
            """SELECT active_labels, gliner_threshold,
                      gliner_enabled, presidio_enabled, classifier_enabled,
                      deanon_enabled, hook_enabled, qwen_auto_approve_threshold
               FROM ner_config WHERE id = 1"""
        )
        return {
            "active_labels": list(row["active_labels"]),
            "gliner_threshold": row["gliner_threshold"],
            "gliner_enabled": row["gliner_enabled"],
            "presidio_enabled": row["presidio_enabled"],
            "classifier_enabled": row["classifier_enabled"],
            "deanon_enabled": row["deanon_enabled"],
            "hook_enabled": row["hook_enabled"],
            "qwen_auto_approve_threshold": row["qwen_auto_approve_threshold"],
        }

    async def update_ner_config(
        self,
        active_labels: list[str],
        gliner_threshold: float,
        gliner_enabled: bool,
        presidio_enabled: bool,
        classifier_enabled: bool,
        deanon_enabled: bool,
        hook_enabled: bool,
        qwen_auto_approve_threshold: float,
    ) -> None:
        await self._pool.execute(
            """UPDATE ner_config
               SET active_labels = $1, gliner_threshold = $2,
                   gliner_enabled = $3, presidio_enabled = $4, classifier_enabled = $5,
                   deanon_enabled = $6, hook_enabled = $7,
                   qwen_auto_approve_threshold = $8,
                   updated_at = NOW()
               WHERE id = 1""",
            active_labels, gliner_threshold,
            gliner_enabled, presidio_enabled, classifier_enabled,
            deanon_enabled, hook_enabled,
            qwen_auto_approve_threshold,
        )

    async def list_patterns(self) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT id, name, regex, entity_label, score, active, source, created_at FROM custom_patterns ORDER BY id"
        )
        return [dict(r) for r in rows]

    async def create_pattern(self, name: str, regex: str, entity_label: str, score: float) -> dict:
        row = await self._pool.fetchrow(
            "INSERT INTO custom_patterns (name, regex, entity_label, score) VALUES ($1, $2, $3, $4) RETURNING id",
            name, regex, entity_label, score,
        )
        return {"id": row["id"], "name": name, "regex": regex, "entity_label": entity_label, "score": score}

    async def upsert_pattern_pending(self, name: str, regex: str, entity_label: str, score: float) -> dict | None:
        """Crée le pattern avec source='qwen' (actif immédiatement, validation data owner requise).
        Retourne None si le regex existe déjà (rien à faire) ou s'il est rejeté par le garde-fou."""
        ok, reason = validate_regex(regex)
        if not ok:
            _logger.warning("pattern.rejected", extra={"pattern": name, "reason": reason})
            return None
        existing = await self._pool.fetchrow(
            "SELECT id FROM custom_patterns WHERE regex = $1", regex
        )
        if existing:
            return None
        row = await self._pool.fetchrow(
            "INSERT INTO custom_patterns (name, regex, entity_label, score, active, source) "
            "VALUES ($1, $2, $3, $4, true, 'qwen') RETURNING id",
            name, regex, entity_label, score,
        )
        return {"id": row["id"], "name": name, "regex": regex, "entity_label": entity_label, "score": score, "active": True, "source": "qwen"}

    async def delete_pattern(self, pattern_id: int) -> None:
        await self._pool.execute("DELETE FROM custom_patterns WHERE id = $1", pattern_id)

    async def list_api_keys(self) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT id, user_id, label, active, created_at FROM api_keys ORDER BY id"
        )
        return [dict(r) for r in rows]

    async def create_api_key(self, label: str) -> dict:
        plain_key = f"anon_{secrets.token_hex(32)}"
        key_hash = _hash_key(plain_key)
        row = await self._pool.fetchrow(
            "INSERT INTO api_keys (key_hash, label) VALUES ($1, $2) RETURNING id, user_id",
            key_hash, label,
        )
        return {"id": row["id"], "user_id": str(row["user_id"]), "label": label, "plain_key": plain_key}

    async def revoke_api_key(self, key_id: int) -> None:
        await self._pool.execute("UPDATE api_keys SET active = false WHERE id = $1", key_id)

# Copyright (c) 2026 David Miguel Loureiro Neri <david@neri.contact>
# Licensed under the PolyForm Noncommercial License 1.0.0.
# See LICENSE file for details.

from __future__ import annotations
import hashlib
import time
from collections import Counter

from sidecar.cache import CacheService
from sidecar.column_classifier import ColumnClassifier
from sidecar.column_labels import ColumnLabelStore
from sidecar.dashboard_events import DashboardEventBus
from sidecar.formats import (
    FieldValue, detect_format, extract_pairs, reinject, augment_pairs_as_text,
)
from sidecar.logging_config import get as _log
from sidecar.ner import NEREngine, NERConfig, Entity

SAMPLE_SIZE = 5
MAX_STRUCT_DEPTH = 5  # garde-fou contre des blobs JSON/XML imbriqués pathologiques
_logger = _log("gateway.anonymizer")


class Anonymizer:
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        gliner_model: str | None = None,
        column_labels: ColumnLabelStore | None = None,
        events_bus: DashboardEventBus | None = None,
        oauth_client=None,
        audit_client=None,
    ):
        kwargs = {"gliner_model": gliner_model} if gliner_model else {}
        self.ner = NEREngine(**kwargs)
        self.classifier = ColumnClassifier(ollama_url=ollama_url, oauth_client=oauth_client)
        self.column_labels = column_labels
        self.events_bus = events_bus
        self.audit_client = audit_client

    async def anonymize(
        self,
        text: str,
        cache: CacheService,
        user_id: str,
        config: NERConfig | None = None,
        context: dict | None = None,
    ) -> tuple[str, dict]:
        fmt = detect_format(text)
        _logger.info("anonymize.start", extra={
            "fmt": fmt, "text_len": len(text), "user": user_id[:8],
            "ctx_db": (context or {}).get("db"),
            "ctx_table": (context or {}).get("table"),
        })
        t0 = time.monotonic()
        if fmt == "freetext":
            result, mapping, entity_counts, sources = await self._anonymize_freetext(
                text, cache, user_id, config
            )
            field_count = None
        else:
            result, mapping, entity_counts, sources, field_count = await self._anonymize_structured(
                text, fmt, cache, user_id, config, context
            )
        latency_ms = int((time.monotonic() - t0) * 1000)

        if self.audit_client is not None:
            try:
                user_id_hash = hashlib.sha256(user_id.encode()).hexdigest()
                text_hash = hashlib.sha256(text.encode()).hexdigest()
                event = {
                    "user_id_hash": user_id_hash,
                    "text_hash": text_hash,
                    "entity_counts": entity_counts,
                    "sources": sources,
                    "latency_ms": latency_ms,
                    "format": fmt,
                    "field_count": field_count,
                    "token_count": len(mapping),
                }
                self.audit_client.record(event)
            except Exception as exc:
                _logger.warning("audit.record_failed", extra={"err": str(exc)})

        return result, mapping

    async def _anonymize_freetext(
        self, text: str, cache: CacheService, user_id: str, config: NERConfig | None = None,
        skip_sql_prepass: bool = False,
    ) -> tuple[str, dict, dict, dict]:
        # Pré-passe : anonymiser les requêtes SQL embarquées dans la prose via l'AST,
        # puis NER sur le texte recollé (le SQL est déjà tokenisé -> non re-taggé).
        sql_mapping: dict = {}
        if not skip_sql_prepass:
            from sidecar.sql_anon import splice_embedded_sql

            async def _anon_sql(sql: str):
                r, mp, *_ = await self._anonymize_structured(sql, "sql", cache, user_id, config, None)
                return r, mp

            text, sql_mapping = await splice_embedded_sql(text, _anon_sql)
        entities = self.ner.detect(text, config)
        replacement: dict[str, str] = {}
        emitted: set[str] = set()
        entity_counts: dict[str, int] = {}
        sources: dict[str, int] = {}
        for entity in entities:
            token = await cache.get_or_create_token(user_id, entity.label, entity.text)
            replacement[entity.text] = token
            entity_counts[entity.label] = entity_counts.get(entity.label, 0) + 1
            sources[entity.source] = sources.get(entity.source, 0) + 1
            if self.events_bus is not None and entity.text not in emitted:
                self.events_bus.emit(
                    token=token, value=entity.text, source=entity.source,
                    label=entity.label, confidence=entity.confidence,
                )
                emitted.add(entity.text)
        result = reinject(text, "freetext", replacement)
        mapping = {v: k for k, v in replacement.items()}
        mapping.update(sql_mapping)
        _logger.info(
            "anonymize.freetext.done",
            extra={"user": user_id[:8], "tokens": len(mapping), "by_label": dict(Counter(e.label for e in entities))},
        )
        return result, mapping, entity_counts, sources

    async def _anonymize_structured(
        self,
        text: str,
        fmt: str,
        cache: CacheService,
        user_id: str,
        config: NERConfig | None = None,
        context: dict | None = None,
        _depth: int = 0,
    ) -> tuple[str, dict, dict, dict, int]:
        ctx_db = (context or {}).get("db")
        ctx_table = (context or {}).get("table")
        pairs = extract_pairs(text, fmt)
        if not pairs:
            result, mapping, entity_counts, sources = await self._anonymize_freetext(
                text, cache, user_id, config, skip_sql_prepass=True
            )
            return result, mapping, entity_counts, sources, 0

        replacement: dict[str, str] = {}
        entity_counts: dict[str, int] = {}
        sources: dict[str, int] = {}
        blob_mapping: dict[str, str] = {}   # {token: valeur} issu des blobs imbriqués (dé-anon)
        blob_values: set[str] = set()       # valeurs remplacées par un blob anonymisé (PAS un token)

        def _track(label: str, source: str) -> None:
            entity_counts[label] = entity_counts.get(label, 0) + 1
            sources[source] = sources.get(source, 0) + 1

        def _emit(token: str, value: str, source: str, label: str, **kwargs) -> None:
            _track(label, source)
            if self.events_bus is not None:
                self.events_bus.emit(token=token, value=value, source=source, label=label, **kwargs)

        # Pré-passe : une valeur qui EST du JSON/XML (cas "JSON/XML dans une colonne")
        # est ré-anonymisée récursivement (blob entier remplacé) au lieu d'être un blob opaque.
        scalar_pairs: list[FieldValue] = []
        for p in pairs:
            v = p.value
            sub_fmt = detect_format(v) if v else "freetext"
            if _depth < MAX_STRUCT_DEPTH and sub_fmt in ("json", "xml") and v not in blob_values:
                try:
                    anon_blob, sub_map, ec, src, _ = await self._anonymize_structured(
                        v, sub_fmt, cache, user_id, config, context, _depth + 1
                    )
                except Exception as e:  # noqa: BLE001 — fail-safe : on retombe en scalaire
                    _logger.warning("structured.nested_failed", extra={"err": str(e), "field": p.field})
                    scalar_pairs.append(p)
                    continue
                replacement[v] = anon_blob
                blob_values.add(v)
                blob_mapping.update(sub_map)
                for k, n in ec.items():
                    entity_counts[k] = entity_counts.get(k, 0) + n
                for k, n in src.items():
                    sources[k] = sources.get(k, 0) + n
            else:
                scalar_pairs.append(p)

        columns: dict[str, list[FieldValue]] = {}
        for p in scalar_pairs:
            columns.setdefault(p.field, []).append(p)

        for field, field_pairs in columns.items():
            values = [p.value for p in field_pairs]

            header_label, match_type = (None, "none")
            if self.column_labels is not None:
                header_label, match_type = await self.column_labels.lookup(
                    field, db=ctx_db, table=ctx_table,
                )

            if header_label:
                _logger.info("column_label.hit", extra={
                    "field": field, "label": header_label, "match": match_type, "user": user_id[:8]
                })
                for v in values:
                    if v and v not in replacement:
                        token = await cache.get_or_create_token(user_id, header_label, v)
                        replacement[v] = token
                        _emit(token, v, f"kb_{match_type}", header_label, field=field, match_type=match_type)
                await self.column_labels.increment_occurrence(field)

                augmented = augment_pairs_as_text(field_pairs)
                if config is None or config.presidio_enabled:
                    from sidecar.ner import PRESIDIO_TO_TOKEN, ALL_LABELS
                    presidio_engine = config.presidio if config else self.ner._base_presidio
                    active_set = config.active_labels if config else ALL_LABELS
                    for r in presidio_engine.analyze(text=augmented, language="fr"):
                        detected = augmented[r.start:r.end]
                        label2 = PRESIDIO_TO_TOKEN.get(r.entity_type, r.entity_type)
                        if detected not in replacement and label2 in active_set:
                            token = await cache.get_or_create_token(user_id, label2, detected)
                            replacement[detected] = token
                            _emit(token, detected, "presidio_safety", label2, field=field, confidence=r.score)
                continue

            augmented = augment_pairs_as_text(field_pairs)
            entities = self.ner.detect(augmented, config)

            for entity in entities:
                if any(entity.text in v for v in values) and entity.text not in replacement:
                    token = await cache.get_or_create_token(user_id, entity.label, entity.text)
                    replacement[entity.text] = token
                    _emit(token, entity.text, entity.source, entity.label, field=field, confidence=entity.confidence)

            unresolved = [
                v for v in values
                if not any(e.text in v for e in entities) and v not in replacement
            ]
            if unresolved and (config is None or config.classifier_enabled):
                sample = unresolved[:SAMPLE_SIZE]
                label, confidence = await self.classifier.classify(
                    table="unknown", column=field, sql_type="varchar", values=sample
                )
                if label:
                    threshold = config.qwen_auto_approve_threshold if config else 0.7
                    status = "active" if confidence >= threshold else "pending"
                    if self.column_labels is not None:
                        try:
                            await self.column_labels.upsert(
                                field, label, source="qwen3",
                                confidence=confidence, status=status,
                                sample_values=sample[:3],
                            )
                        except Exception as e:
                            _logger.warning("column_label.upsert_failed", extra={"field": field, "err": str(e)})
                    for v in unresolved:
                        if v not in replacement:
                            token = await cache.get_or_create_token(user_id, label, v)
                            replacement[v] = token
                            _emit(token, v, "qwen3", label, field=field, confidence=confidence, match_type=status)
                else:
                    _logger.info(
                        "anonymize.structured.column_kept",
                        extra={"user": user_id[:8], "field": field, "unresolved": len(unresolved)},
                    )

        result = reinject(text, fmt, replacement)
        # Découplage : la dé-anon ne contient QUE des tokens scalaires (inversion de
        # replacement hors blobs) + les mappings internes des blobs. La paire
        # blob→blob_anonymisé n'est PAS un token de dé-anon.
        mapping = {tok: val for val, tok in replacement.items() if val not in blob_values}
        mapping.update(blob_mapping)
        _logger.info(
            "anonymize.structured.done",
            extra={"user": user_id[:8], "fmt": fmt, "tokens": len(mapping),
                   "fields": len(columns), "blobs": len(blob_values)},
        )
        return result, mapping, entity_counts, sources, len(columns)

    def deanonymize(self, text: str, mapping: dict) -> str:
        result = text
        for token, original in sorted(mapping.items(), key=lambda x: len(x[0]), reverse=True):
            result = result.replace(token, original)
        return result

    async def aclose(self) -> None:
        await self.classifier.aclose()

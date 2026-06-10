# Copyright (c) 2026 David Miguel Loureiro Neri <david@neri.contact>
# Licensed under the PolyForm Noncommercial License 1.0.0.
# See LICENSE file for details.

from __future__ import annotations
import os
import re
from dataclasses import dataclass
from gliner import GLiNER
from sidecar.logging_config import get as _log

_logger = _log("gateway.ner")
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_analyzer.nlp_engine import NlpEngineProvider

GLINER_MODEL = os.environ.get("GLINER_MODEL", "urchade/gliner_multi_pii-v1")

GLINER_LABELS = [
    "person", "date", "location", "organization",
    "avs number", "iban", "phone number", "email address",
    "policy number", "contract number", "license plate",
]

GLINER_TO_TOKEN = {
    "person": "PERSONNE",
    "date": "DATE",
    "location": "LOCALISATION",
    "organization": "ORG",
    "avs number": "AVS",
    "iban": "IBAN",
    "phone number": "TEL",
    "email address": "EMAIL",
    "policy number": "POLICE",
    "contract number": "CONTRAT",
    "license plate": "PLAQUE",
    "vin": "VIN",
    "permis": "PERMIS",
}

PRESIDIO_TO_TOKEN = {
    "PERSON": "PERSONNE",
    "DATE_TIME": "DATE",
    "LOCATION": "LOCALISATION",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "TEL",
    "IBAN_CODE": "IBAN",
    "AVS_NUMBER": "AVS",
    "POLICY_NUMBER": "POLICE",
    "CONTRACT_NUMBER": "CONTRAT",
    "PLAQUE": "PLAQUE",
    "VIN_NUMBER": "VIN",
    "PERMIS": "PERMIS",
}

ALL_LABELS = frozenset(GLINER_TO_TOKEN.values())


@dataclass
class Entity:
    text: str
    label: str
    start: int
    end: int
    source: str = "unknown"  # "gliner" | "presidio" — renseigné par NEREngine.detect
    confidence: float | None = None


@dataclass
class NERConfig:
    active_labels: frozenset
    gliner_threshold: float
    presidio: AnalyzerEngine
    gliner_enabled: bool = True
    presidio_enabled: bool = True
    classifier_enabled: bool = True
    qwen_auto_approve_threshold: float = 0.7

    @staticmethod
    def default(engine: "NEREngine") -> "NERConfig":
        return NERConfig(
            active_labels=ALL_LABELS,
            gliner_threshold=0.5,
            presidio=engine._base_presidio,
        )

    @staticmethod
    def build(
        active_labels: list[str],
        gliner_threshold: float,
        extra_patterns: list[dict],
        base_engine: "NEREngine",
        gliner_enabled: bool = True,
        presidio_enabled: bool = True,
        classifier_enabled: bool = True,
        qwen_auto_approve_threshold: float = 0.7,
    ) -> "NERConfig":
        presidio = base_engine._build_presidio(extra_patterns)
        return NERConfig(
            active_labels=frozenset(active_labels),
            gliner_threshold=gliner_threshold,
            presidio=presidio,
            gliner_enabled=gliner_enabled,
            presidio_enabled=presidio_enabled,
            classifier_enabled=classifier_enabled,
            qwen_auto_approve_threshold=qwen_auto_approve_threshold,
        )


class NEREngine:
    def __init__(self, gliner_model: str = GLINER_MODEL):
        self.gliner = GLiNER.from_pretrained(gliner_model)
        self._base_presidio = self._build_presidio([])

    def _build_presidio(self, extra_patterns: list[dict]) -> AnalyzerEngine:
        avs = PatternRecognizer(
            supported_entity="AVS_NUMBER",
            supported_language="*",
            patterns=[Pattern("AVS", r"756\.\d{4}\.\d{4}\.\d{2}", 0.9)],
        )
        policy = PatternRecognizer(
            supported_entity="POLICY_NUMBER",
            supported_language="*",
            patterns=[Pattern("POLICY", r"\bPO\d{9}-\d{5}/\d{2}\b", 0.9)],
        )
        contract = PatternRecognizer(
            supported_entity="CONTRACT_NUMBER",
            supported_language="*",
            patterns=[Pattern("CONTRACT", r"\bCT-\d{5,10}\b", 0.75)],
        )
        vin = PatternRecognizer(
            supported_entity="VIN_NUMBER",
            supported_language="*",
            patterns=[Pattern("VIN", r"\b[A-HJ-NPR-Z0-9]{17}\b", 0.8)],
        )
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "fr", "model_name": "fr_core_news_md"}],
        })
        engine = AnalyzerEngine(
            nlp_engine=provider.create_engine(),
            supported_languages=["fr"],
        )
        # Supprimer les reconnaisseurs intégrés :
        # - Phone : faux positifs sur les dates
        # - SpacyRecognizer : redondant avec GLiNER, génère du MISC inutile
        for name in ("PhoneRecognizer", "PhoneNumberRecognizer", "SpacyRecognizer"):
            try:
                engine.registry.remove_recognizer(name)
            except Exception:
                pass
        engine.registry.add_recognizer(avs)
        engine.registry.add_recognizer(policy)
        engine.registry.add_recognizer(contract)
        engine.registry.add_recognizer(vin)
        for p in extra_patterns:
            # Défense en profondeur : la KB peut contenir des patterns antérieurs
            # au garde-fou gateway (regex_guard) — on ignore ce qui ne compile pas.
            try:
                re.compile(p["regex"])
            except re.error as e:
                _logger.warning("pattern.skipped", extra={"pattern": p["name"], "err": str(e)})
                continue
            recognizer = PatternRecognizer(
                supported_entity=p["entity_label"],
                supported_language="*",
                patterns=[Pattern(p["name"], p["regex"], p["score"])],
            )
            engine.registry.add_recognizer(recognizer)
        return engine

    def detect(self, text: str, config: NERConfig | None = None) -> list[Entity]:
        if config is None:
            config = NERConfig.default(self)

        covered: set[tuple[int, int]] = set()
        entities: list[Entity] = []
        gliner_hits: list[dict] = []
        presidio_hits: list[dict] = []

        # Presidio en premier : patterns regex précis (AVS, IBAN, police, contrat)
        if config.presidio_enabled:
            for r in config.presidio.analyze(text=text, language="fr"):
                span = (r.start, r.end)
                label = PRESIDIO_TO_TOKEN.get(r.entity_type)
                if not label:
                    label = r.entity_type
                if label and label in config.active_labels and span not in covered:
                    entities.append(Entity(
                        text=text[r.start:r.end], label=label,
                        start=r.start, end=r.end,
                        source="presidio", confidence=r.score,
                    ))
                    covered.add(span)
                    presidio_hits.append({"label": label, "entity_type": r.entity_type, "span": list(span)})

        # GLiNER en second : détection contextuelle sur les spans non encore couverts
        # TODO(ner-1char-fp) : GLiNER hallucine parfois des entités d'un seul
        # caractère, typiquement des alias SQL (`SELECT c.prenom FROM clients c`
        # → la lettre `c` est détectée comme PERSONNE). Observé 1/5 dry-runs
        # scène 2 post-phase-5 (commit 2c71d08f). Fix proposé : filtrer
        # `len(e["text"].strip()) >= 2` au minimum, mieux : whitelister les
        # alias SQL via NERConfig en contexte query_db. Voir checkpoint
        # 2026_05_25 dans la session memory pour le diagnostic complet.
        if config.gliner_enabled:
            for e in self.gliner.predict_entities(text, GLINER_LABELS, threshold=config.gliner_threshold):
                span = (e["start"], e["end"])
                label = GLINER_TO_TOKEN.get(e["label"])
                score = e.get("score", 1.0)
                if label and label in config.active_labels and span not in covered and score >= config.gliner_threshold:
                    entities.append(Entity(
                        text=e["text"], label=label,
                        start=e["start"], end=e["end"],
                        source="gliner", confidence=score,
                    ))
                    covered.add(span)
                    gliner_hits.append({"label": label, "score": round(score, 3), "span": list(span)})

        _logger.info(
            "ner.detect",
            extra={
                "text_len": len(text),
                "threshold": config.gliner_threshold,
                "gliner": gliner_hits,
                "presidio": presidio_hits,
                "total": len(entities),
            },
        )
        return entities

"""Stubs pour les dépendances ML lourdes — permet de lancer les tests sans Docker.

gliner, spacy et presidio_analyzer sont mockés avant tout import.
Les tests qui testent la logique NER réelle utilisent leurs propres mocks
(unittest.mock) par-dessus ces stubs.
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock


def _stub(name: str, **attrs) -> ModuleType:
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# ── gliner ────────────────────────────────────────────────────────────────────
_stub("gliner", GLiNER=MagicMock())

# ── spacy ─────────────────────────────────────────────────────────────────────
_stub("spacy")

# ── presidio_analyzer ─────────────────────────────────────────────────────────
_stub("presidio_analyzer",
      AnalyzerEngine=MagicMock(),
      PatternRecognizer=MagicMock(),
      Pattern=MagicMock())
_stub("presidio_analyzer.nlp_engine",
      NlpEngineProvider=MagicMock())

"""Défense en profondeur : un pattern KB non compilable est ignoré, pas exécuté.

Le garde-fou gateway (regex_guard) bloque les nouveaux patterns invalides à
l'insertion, mais la KB peut déjà contenir des patterns antérieurs au garde-fou.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from sidecar.ner import NEREngine


def _build_with_patterns(extra_patterns):
    engine = NEREngine.__new__(NEREngine)  # sans __init__ : pas de download GLiNER
    return engine._build_presidio(extra_patterns)


def test_invalid_kb_regex_is_skipped():
    good = {"name": "PO", "regex": r"\bPO\d{9}\b", "entity_label": "NUM_POLICE", "score": 0.9}
    bad = {"name": "BROKEN", "regex": r"[unclosed", "entity_label": "X", "score": 0.5}
    with patch("sidecar.ner.AnalyzerEngine") as mock_engine_cls, \
         patch("sidecar.ner.NlpEngineProvider"):
        registry = mock_engine_cls.return_value.registry
        _build_with_patterns([bad, good])
    # 4 reconnaisseurs de base (AVS, policy, contract, VIN) + le seul pattern valide
    assert registry.add_recognizer.call_count == 5


def test_all_valid_kb_regexes_are_added():
    p1 = {"name": "A", "regex": r"\d{4}", "entity_label": "X", "score": 0.5}
    p2 = {"name": "B", "regex": r"[A-Z]{3}-\d+", "entity_label": "Y", "score": 0.5}
    with patch("sidecar.ner.AnalyzerEngine") as mock_engine_cls, \
         patch("sidecar.ner.NlpEngineProvider"):
        registry = mock_engine_cls.return_value.registry
        _build_with_patterns([p1, p2])
    assert registry.add_recognizer.call_count == 6

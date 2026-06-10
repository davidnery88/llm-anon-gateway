"""Tests proxy_anonymizer — couverture du masking sentinel anti-cascade.

Sans masking, NER ré-anonymise les placeholders embarqués dans du texte
naturel (assistant rejouant `[PERSONNE_156]` dans son prochain tour →
crée `[PERSONNE_163] -> "[PERSONNE_156]"` dans Redis, et le deanon ne
peut pas suivre l'indirection). Le masking remplace les placeholders
par des sentinels `\\x00PH<n>\\x00` invisibles à NER, et restaure ensuite.
"""
from __future__ import annotations

from sidecar.proxy_anonymizer import _mask_placeholders, _unmask_placeholders


def test_mask_unmask_roundtrip():
    text = "Top 3 clients : [PERSONNE_156], [PERSONNE_157], [IBAN_42]."
    masked, sentinels = _mask_placeholders(text)
    assert sentinels == ["[PERSONNE_156]", "[PERSONNE_157]", "[IBAN_42]"]
    # Les placeholders ont disparu du texte donné à NER
    assert "[PERSONNE" not in masked
    assert "[IBAN" not in masked
    # Restoration parfaite
    restored = _unmask_placeholders(masked, sentinels)
    assert restored == text


def test_mask_preserves_non_placeholder_text():
    text = "Salut [link](url) et [1,2,3] !"
    masked, sentinels = _mask_placeholders(text)
    # Brackets sans format LABEL_N ne sont pas masqués
    assert sentinels == []
    assert masked == text


def test_unmask_handles_empty():
    assert _unmask_placeholders("plain text", []) == "plain text"


def test_mask_sentinel_is_json_safe():
    """Le sentinel doit être pur ASCII sans caractère de contrôle pour
    survivre un round-trip JSON (Anthropic refuse \\x00 dans les strings)."""
    import json
    masked, sentinels = _mask_placeholders("Bonjour [PERSONNE_1] !")
    # Round-trip JSON sans perte
    roundtrip = json.loads(json.dumps({"t": masked}))["t"]
    restored = _unmask_placeholders(roundtrip, sentinels)
    assert restored == "Bonjour [PERSONNE_1] !"


def test_unmask_resilient_to_index_only():
    """Si NER ajoute du whitespace dans le sentinel, le regex sur l'index
    reste capable de le retrouver."""
    masked, sentinels = _mask_placeholders("Top : [PERSONNE_1] et [PERSONNE_2]")
    # NER pourrait théoriquement laisser intact mais on simule pas de drift
    restored = _unmask_placeholders(masked, sentinels)
    assert restored == "Top : [PERSONNE_1] et [PERSONNE_2]"


def test_system_prompt_is_skipped():
    """Le champ system n'est PAS anonymisé (gain perf -1 à -2s par tour).

    Risque documenté : si CLAUDE.md contient du PII, il fuitera.
    Voir docs/SECURITY.md pour la justification."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from sidecar.proxy_anonymizer import PayloadAnonymizer

    anon = MagicMock()
    anon.anonymize = AsyncMock(side_effect=lambda text, *a, **kw: (f"ANON({text})", {}))
    cache = MagicMock()
    pa = PayloadAnonymizer(anonymizer=anon, cache=cache, user_id="test")

    body = b'{"system": "Tu es un assistant. Contact: david@example.com", "messages": [{"role": "user", "content": "Bonjour"}]}'
    result = asyncio.run(pa.anonymize_body(body))
    payload = __import__("json").loads(result)

    assert payload["system"] == "Tu es un assistant. Contact: david@example.com"
    assert payload["messages"][0]["content"] == "ANON(Bonjour)"


def _make_payload_anonymizer():
    import json as _json
    from unittest.mock import AsyncMock, MagicMock

    from sidecar.proxy_anonymizer import PayloadAnonymizer

    anon = MagicMock()
    anon.anonymize = AsyncMock(side_effect=lambda text, *a, **kw: (f"ANON({text})", {}))
    return PayloadAnonymizer(anonymizer=anon, cache=MagicMock(), user_id="test")


def test_image_block_is_rejected():
    """Fail-closed : un bloc image ne peut pas être anonymisé (pas d'OCR local),
    on refuse la requête plutôt que de forwarder le contenu en clair."""
    import asyncio
    import json

    import pytest as _pytest

    from sidecar.proxy_anonymizer import UnsupportedBlockError

    pa = _make_payload_anonymizer()
    body = json.dumps({
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        ]}],
    }).encode()
    with _pytest.raises(UnsupportedBlockError):
        asyncio.run(pa.anonymize_body(body))


def test_document_block_is_rejected():
    import asyncio
    import json

    import pytest as _pytest

    from sidecar.proxy_anonymizer import UnsupportedBlockError

    pa = _make_payload_anonymizer()
    body = json.dumps({
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "AAAA"}},
        ]}],
    }).encode()
    with _pytest.raises(UnsupportedBlockError):
        asyncio.run(pa.anonymize_body(body))


def test_unknown_text_like_block_still_passes_through():
    """Les types inconnus non binaires (ex. thinking) restent pass-through :
    ils ne contiennent que du texte généré par le modèle (déjà placeholderisé)."""
    import asyncio
    import json

    pa = _make_payload_anonymizer()
    body = json.dumps({
        "messages": [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": "réflexion [PERSONNE_1]", "signature": "x"},
        ]}],
    }).encode()
    result = asyncio.run(pa.anonymize_body(body))
    payload = json.loads(result)
    assert payload["messages"][0]["content"][0]["thinking"] == "réflexion [PERSONNE_1]"

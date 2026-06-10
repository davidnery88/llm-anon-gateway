"""Tests StreamDeanonymizer — couverture du fix race condition (lazy lookup).

Le proxy charge la mapping Redis UNE FOIS au début du stream. Si un tool
call crée des tokens pendant le stream, ils ne sont pas dans le snapshot
initial. Le fix : lookup à la volée quand un placeholder inconnu est
rencontré, avec cache négatif pour éviter de retaper Redis sur des
faux-positifs (texte qui ressemble à un placeholder mais n'en est pas).
"""
from __future__ import annotations

import pytest

from sidecar.proxy_deanonymizer import StreamDeanonymizer


@pytest.mark.asyncio
async def test_known_mapping_replaced():
    deanon = StreamDeanonymizer({"[PERSONNE_1]": "Julien Maillard"})
    out = await deanon.feed("Salut [PERSONNE_1] !")
    out += await deanon.flush_final()
    assert out == "Salut Julien Maillard !"


@pytest.mark.asyncio
async def test_lazy_lookup_resolves_unknown():
    """Snapshot initial vide ; un placeholder créé pendant le stream
    est résolu via lookup."""
    redis_state = {"[PERSONNE_1]": "Marie Dubois"}
    calls = []

    async def lookup(token: str):
        calls.append(token)
        return redis_state.get(token)

    deanon = StreamDeanonymizer({}, cache_lookup=lookup)
    out = await deanon.feed("Le client est [PERSONNE_1].")
    out += await deanon.flush_final()
    assert out == "Le client est Marie Dubois."
    assert calls == ["[PERSONNE_1]"]


@pytest.mark.asyncio
async def test_negative_lookup_is_cached():
    """Un faux placeholder (texte qui ressemble) ne doit déclencher
    qu'un seul hget, même s'il apparaît dans plusieurs chunks."""
    calls = []

    async def lookup(token: str):
        calls.append(token)
        return None

    deanon = StreamDeanonymizer({}, cache_lookup=lookup)
    out = await deanon.feed("Voir [REF_DOC_42] et ")
    out += await deanon.feed("encore [REF_DOC_42] plus loin.")
    out += await deanon.flush_final()
    assert "[REF_DOC_42]" in out  # laissé tel quel, pas dans mapping
    assert calls == ["[REF_DOC_42]"]  # un seul appel Redis


@pytest.mark.asyncio
async def test_lazy_lookup_preserves_sort_invariant():
    """Quand on ajoute lazy un token court alors qu'un token long existe,
    le long doit toujours être remplacé en premier (sinon [PERSONNE_10]
    devient 'Alice0' au lieu de 'Bob')."""
    redis_state = {"[PERSONNE_1]": "Alice", "[PERSONNE_10]": "Bob"}

    async def lookup(token: str):
        return redis_state.get(token)

    deanon = StreamDeanonymizer({"[PERSONNE_1]": "Alice"}, cache_lookup=lookup)
    # PERSONNE_10 inconnu au snapshot, résolu lazy
    out = await deanon.feed("[PERSONNE_10] et [PERSONNE_1] discutent.")
    out += await deanon.flush_final()
    assert out == "Bob et Alice discutent."


@pytest.mark.asyncio
async def test_no_cache_lookup_falls_back_to_snapshot():
    """Si pas de cache_lookup fourni, comportement legacy : unknowns
    restent en placeholders."""
    deanon = StreamDeanonymizer({"[PERSONNE_1]": "Alice"})
    out = await deanon.feed("[PERSONNE_1] et [PERSONNE_2] parlent.")
    out += await deanon.flush_final()
    assert out == "Alice et [PERSONNE_2] parlent."


@pytest.mark.asyncio
async def test_partial_placeholder_across_chunks_with_lookup():
    """Régression : un placeholder à cheval doit être correctement
    bufferisé puis résolu lazy."""
    redis_state = {"[IBAN_3]": "CH56 0023"}

    async def lookup(token: str):
        return redis_state.get(token)

    deanon = StreamDeanonymizer({}, cache_lookup=lookup)
    out = await deanon.feed("Le compte [IBA")
    out += await deanon.feed("N_3] est OK.")
    out += await deanon.flush_final()
    assert out == "Le compte CH56 0023 est OK."


@pytest.mark.asyncio
async def test_lone_bracket_at_chunk_boundary():
    """Anthropic splitte parfois `[PERSONNE_X]` entre 2 SSE deltas tels
    que le premier finit par `[` seul. Sans buffering du `[`, le delta
    suivant `PERSONNE_X]` est invisible (regex full requiert `[` initial)."""
    deanon = StreamDeanonymizer({"[PERSONNE_7]": "Julien Maillard"})
    out = await deanon.feed("Bonjour [")
    out += await deanon.feed("PERSONNE_7] !")
    out += await deanon.flush_final()
    assert out == "Bonjour Julien Maillard !"


@pytest.mark.asyncio
async def test_lone_bracket_followed_by_non_placeholder():
    """Un `[` isolé suivi par du texte non-placeholder (markdown link) doit
    bien finir par être flushé tel quel."""
    deanon = StreamDeanonymizer({})
    out = await deanon.feed("voir [")
    out += await deanon.feed("link](url) plus loin.")
    out += await deanon.flush_final()
    assert out == "voir [link](url) plus loin."


# ===== feed_sse : parsing structuré des events SSE =====

import re as _re
import json as _json


def _extract_visible_text(sse_bytes: bytes) -> str:
    """Reconstruit le texte visible à l'utilisateur en concaténant tous
    les `delta.text` des events text_delta dans le flux SSE."""
    visible = []
    for block in sse_bytes.split(b"\n\n"):
        for line in block.split(b"\n"):
            if line.startswith(b"data: "):
                try:
                    payload = _json.loads(line[6:])
                except _json.JSONDecodeError:
                    continue
                if (
                    isinstance(payload, dict)
                    and payload.get("type") == "content_block_delta"
                ):
                    delta = payload.get("delta", {})
                    if isinstance(delta, dict) and delta.get("type") == "text_delta":
                        visible.append(delta.get("text", ""))
    return "".join(visible)


@pytest.mark.asyncio
async def test_sse_text_delta_replaces_placeholder():
    """text_delta avec placeholder connu → remplacement dans le JSON."""
    deanon = StreamDeanonymizer({"[PERSONNE_1]": "Julien Maillard"})
    event = (
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"Hello [PERSONNE_1] !"}}\n\n'
    )
    out = await deanon.feed_sse(event)
    out += await deanon.flush_final_sse()
    assert _extract_visible_text(out) == "Hello Julien Maillard !"


@pytest.mark.asyncio
async def test_sse_placeholder_split_across_events():
    """Le cas qui faisait fuiter 1/4 des runs scène 3 : `[` dans un delta,
    `PERSONNE_X]` dans le suivant, séparés par les délimiteurs SSE/JSON."""
    deanon = StreamDeanonymizer({"[PERSONNE_7]": "Julien Maillard"})
    start = (
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":0,'
        b'"content_block":{"type":"text","text":""}}\n\n'
    )
    delta1 = (
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"Bonjour ["}}\n\n'
    )
    delta2 = (
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"PERSONNE_7] !"}}\n\n'
    )
    out = await deanon.feed_sse(start)
    out += await deanon.feed_sse(delta1)
    out += await deanon.feed_sse(delta2)
    out += await deanon.flush_final_sse()
    assert _extract_visible_text(out) == "Bonjour Julien Maillard !"


@pytest.mark.asyncio
async def test_sse_non_data_events_pass_through():
    """Les events qui ne sont pas data: (ping, message_stop, etc.)
    passent verbatim — pas d'altération."""
    deanon = StreamDeanonymizer({})
    event = b"event: ping\ndata: {}\n\n"
    out = await deanon.feed_sse(event)
    assert out == event


@pytest.mark.asyncio
async def test_sse_arbitrary_byte_chunking():
    """Les chunks TCP peuvent splitter les bytes n'importe où. Le buffer
    doit réassembler avant de chercher `\\n\\n`."""
    deanon = StreamDeanonymizer({"[PERSONNE_1]": "David"})
    event = (
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"Hello [PERSONNE_1] !"}}\n\n'
    )
    # Split mid-byte sequence, multiple chunks
    out = await deanon.feed_sse(event[:25])
    out += await deanon.feed_sse(event[25:60])
    out += await deanon.feed_sse(event[60:])
    out += await deanon.flush_final_sse()
    assert _extract_visible_text(out) == "Hello David !"


@pytest.mark.asyncio
async def test_sse_lazy_lookup_works_in_sse_mode():
    """Snapshot vide + lookup async : un placeholder résolu lazy en
    mode SSE doit fonctionner comme en mode texte."""
    redis_state = {"[PERSONNE_42]": "Marie"}

    async def lookup(token: str):
        return redis_state.get(token)

    deanon = StreamDeanonymizer({}, cache_lookup=lookup)
    event = (
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"Salut [PERSONNE_42]"}}\n\n'
    )
    out = await deanon.feed_sse(event)
    out += await deanon.flush_final_sse()
    assert _extract_visible_text(out) == "Salut Marie"


@pytest.mark.asyncio
async def test_sse_held_text_flushed_via_synthetic_event():
    """Si la fin du stream laisse du texte dans le buffer (queue
    bufferisée), `flush_final_sse` doit émettre un text_delta synthétique
    sur le dernier index de bloc texte."""
    deanon = StreamDeanonymizer({})
    start = (
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":0,'
        b'"content_block":{"type":"text","text":""}}\n\n'
    )
    # Texte qui finit par un `[` retenu sans suite — flush_final doit
    # l'émettre comme texte normal (pas un placeholder, donc passe).
    delta = (
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"fin ["}}\n\n'
    )
    out = await deanon.feed_sse(start)
    out += await deanon.feed_sse(delta)
    out += await deanon.flush_final_sse()
    assert _extract_visible_text(out) == "fin ["

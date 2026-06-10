# Copyright (c) 2026 David Miguel Loureiro Neri <david@neri.contact>
# Licensed under the PolyForm Noncommercial License 1.0.0.
# See LICENSE file for details.

r"""Phase 3 du plan PROXY — désanonymisation streaming des réponses Anthropic.

Les réponses SSE arrivent en chunks. Un placeholder comme `[PERSONNE_12]`
peut être coupé en plein milieu entre deux chunks (`[PERSON` + `NE_12]`).
On ne peut donc pas faire un simple `replace` chunk par chunk : il faut
buffer la queue qui *pourrait* être le début d'un placeholder, et flush
le reste.

Algo :
1. À chaque chunk reçu (texte UTF-8 décodé) :
   - Ajoute au buffer
   - Si un cache_lookup est fourni : scan les placeholders *complets* inconnus
     dans le buffer, lookup Redis ciblé via hget (résout la race où un tool
     call crée des tokens pendant le stream — voir checkpoint 2026-05-25)
   - Remplace tous les placeholders complets connus
   - Cherche un préfixe partiel à la queue via regex `\[[A-Z_]+(?:_\d*)?$`
   - Flush tout ce qui précède la queue, garde la queue pour le prochain chunk
2. À la fin du stream : un flush_final() vide tout y compris une queue
   restante (si jamais un placeholder finissait à la limite, peu probable).

Le regex de queue est strict (préfixe `[`, majuscules + underscores,
optionnellement suivi de `_chiffres`) pour ne PAS bufferiser indéfiniment
sur du markdown type `[link](url)`, des listes Python `[1,2,3]`, etc.

Décodage UTF-8 incrémental côté appelant (un chunk peut couper un codepoint
multi-octet) — voir proxy.py qui utilise codecs.getincrementaldecoder.
"""
from __future__ import annotations

import json
import re
from typing import Awaitable, Callable, Optional

# Placeholder complet : [LABEL_N] avec LABEL en majuscules/underscores.
_PLACEHOLDER_FULL = re.compile(r'\[[A-Z][A-Z_]*_\d+\]')

# Préfixe potentiel à la queue du buffer (placeholder en cours de découpe).
# Anthropic splitte parfois `[PERSONNE_X]` entre 2 SSE deltas (`[` dans
# l'un, `PERSONNE_X]` dans l'autre). On retient même un `[` isolé pour
# fusionner avec le delta suivant — quitte à délayer d'un chunk un
# crochet ouvrant légitime (markdown, listes), qui sera flushé dès qu'on
# voit que la suite n'est pas un placeholder.
_PLACEHOLDER_TAIL = re.compile(r'\[(?:[A-Z][A-Z_]*(?:_\d*)?)?$')


CacheLookup = Callable[[str], Awaitable[Optional[str]]]


class StreamDeanonymizer:
    def __init__(
        self,
        mapping: dict[str, str],
        cache_lookup: Optional[CacheLookup] = None,
    ):
        """mapping: snapshot initial placeholder → original.
        cache_lookup: closure async optionnelle qui résout les placeholders
        inconnus à la volée (hget Redis ciblé). Permet de fermer la race
        où un tool call crée de nouveaux tokens pendant le stream de
        réponse, après que le snapshot a été chargé.
        """
        # Trié par longueur décroissante pour que [PERSONNE_10] soit remplacé
        # avant [PERSONNE_1] (sinon "[PERSONNE_10]" deviendrait "Julien Maillard0").
        self._tokens: list[tuple[str, str]] = sorted(
            mapping.items(), key=lambda x: -len(x[0])
        )
        self._known: set[str] = set(mapping.keys())
        self._queried: set[str] = set()  # placeholders déjà tentés (hit ou miss)
        self._buffer = ""
        self._lookup = cache_lookup
        # Mode SSE : buffer brut + dernier index de bloc texte connu (pour
        # émettre un text_delta synthétique au flush final si du texte reste
        # dans le buffer logique).
        self._sse_buffer: bytes = b""
        self._last_text_index: Optional[int] = None

    def _add_token(self, placeholder: str, original: str) -> None:
        """Insère en gardant l'invariant tri par longueur décroissante."""
        item = (placeholder, original)
        idx = 0
        for existing in self._tokens:
            if len(existing[0]) < len(placeholder):
                break
            idx += 1
        self._tokens.insert(idx, item)
        self._known.add(placeholder)

    async def _resolve_unknowns(self) -> None:
        if self._lookup is None:
            return
        # Tous les placeholders complets du buffer, dédupliqués
        candidates = set(_PLACEHOLDER_FULL.findall(self._buffer))
        for token in candidates:
            if token in self._known or token in self._queried:
                continue
            self._queried.add(token)
            try:
                value = await self._lookup(token)
            except Exception:
                value = None
            if value is not None:
                self._add_token(token, value)

    async def feed(self, chunk: str) -> str:
        """Reçoit un chunk, retourne le texte prêt à être flushé."""
        if not chunk:
            return ""

        self._buffer += chunk

        # 1. Résout les placeholders inconnus via lookup (race fix)
        await self._resolve_unknowns()

        # 2. Remplace tous les placeholders complets actuels
        for placeholder, original in self._tokens:
            if placeholder in self._buffer:
                self._buffer = self._buffer.replace(placeholder, original)

        # 3. Cherche un préfixe partiel en fin de buffer
        m = _PLACEHOLDER_TAIL.search(self._buffer)
        if m and m.end() == len(self._buffer):
            to_flush = self._buffer[: m.start()]
            self._buffer = self._buffer[m.start() :]
        else:
            to_flush = self._buffer
            self._buffer = ""

        return to_flush

    async def flush_final(self) -> str:
        """Vide tout — à appeler à la fin du stream."""
        # Dernière passe lookup + replace au cas où un placeholder serait
        # resté entier dans la queue.
        await self._resolve_unknowns()
        for placeholder, original in self._tokens:
            if placeholder in self._buffer:
                self._buffer = self._buffer.replace(placeholder, original)
        final = self._buffer
        self._buffer = ""
        return final

    # ── Mode SSE-aware (proxy.py utilise ces deux-là) ──────────────────────

    async def feed_sse(self, chunk: bytes) -> bytes:
        """Variante SSE-structurée de feed(). Découpe le stream en events
        (délimités par `\\n\\n`), extrait `delta.text` des text_delta,
        applique feed() sur ce texte logique, puis ré-émet l'event avec
        le texte remplacé.

        Ferme le bug du split inter-deltas : si `[PERSONNE_X]` est coupé
        entre 2 events SSE (`[` dans l'un, `PERSONNE_X]` dans l'autre,
        avec délimiteurs JSON entre les deux), le buffer textuel
        accumule les deux fragments et résout proprement.

        Les autres events (message_start, ping, content_block_stop, etc.)
        passent verbatim.
        """
        if not chunk:
            return b""
        self._sse_buffer += chunk
        output = b""
        while True:
            idx = self._sse_buffer.find(b"\n\n")
            if idx == -1:
                break
            event_block = self._sse_buffer[: idx + 2]
            self._sse_buffer = self._sse_buffer[idx + 2 :]
            output += await self._process_sse_event(event_block)
        return output

    async def flush_final_sse(self) -> bytes:
        """Termine le stream SSE :
        - process l'éventuel event incomplet restant dans _sse_buffer
        - flush le buffer logique du deanon ; si du texte reste, on
          émet un text_delta synthétique sur le dernier `index` connu
          d'un bloc texte. Sans index connu, on jette (rien de visible
          n'aurait pu produire ce texte de toute façon).
        """
        output = b""
        if self._sse_buffer:
            output += await self._process_sse_event(self._sse_buffer)
            self._sse_buffer = b""
        remaining = await self.flush_final()
        if remaining and self._last_text_index is not None:
            output += self._build_text_delta_event(self._last_text_index, remaining)
        return output

    async def _process_sse_event(self, event_block: bytes) -> bytes:
        """Traite un event SSE complet (ou partiel en fin de stream).
        Si c'est un text_delta, remplace `delta.text` par le texte
        désanonymisé. Sinon, passe verbatim."""
        lines = event_block.split(b"\n")
        data_idx = None
        for i, line in enumerate(lines):
            if line.startswith(b"data: "):
                data_idx = i
                break
        if data_idx is None:
            return event_block

        try:
            payload = json.loads(lines[data_idx][6:])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return event_block

        if not isinstance(payload, dict):
            return event_block

        # Mémorise l'index du bloc texte pour pouvoir émettre un
        # text_delta synthétique au flush final si nécessaire.
        if payload.get("type") == "content_block_start":
            block = payload.get("content_block", {})
            if isinstance(block, dict) and block.get("type") == "text":
                idx_val = payload.get("index")
                if isinstance(idx_val, int):
                    self._last_text_index = idx_val

        # Désanonymise text_delta.
        if payload.get("type") == "content_block_delta":
            delta = payload.get("delta", {})
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                original = delta.get("text", "")
                if isinstance(original, str):
                    new_text = await self.feed(original)
                    delta["text"] = new_text
                    new_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    lines[data_idx] = b"data: " + new_data
                    return b"\n".join(lines)

        return event_block

    def _build_text_delta_event(self, index: int, text: str) -> bytes:
        """Construit un event SSE text_delta synthétique pour émettre du
        texte retenu dans le buffer logique à la fin du stream."""
        payload = {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }
        data = json.dumps(payload, ensure_ascii=False)
        return f"event: content_block_delta\ndata: {data}\n\n".encode("utf-8")

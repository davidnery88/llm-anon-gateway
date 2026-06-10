"""Bus d'événements de détection pour le dashboard de démo.

Stockage in-memory + diffusion SSE. Volontairement minimaliste — c'est de
l'observabilité de démo, pas du télémétrique prod : pas de persistence,
pas d'auth, pas de garantie de delivery, et un buffer circulaire fixe.

Aucune valeur PII ne quitte le sidecar : tout est en local. Le dashboard
HTML s'abonne en SSE et reçoit les events comme du JSON.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import AsyncIterator


@dataclass
class DetectionEvent:
    ts: float           # epoch float, secondes UTC
    token: str          # ex "[PERSONNE_1]"
    value: str          # ex "Julien Maillard" (truncated si très long)
    source: str         # "gliner" | "presidio" | "kb_static" | "kb_fuzzy" | "qwen3"
    label: str          # ex "PERSONNE"
    confidence: float | None = None
    field: str | None = None    # nom de colonne pour les détections structurées
    match_type: str | None = None  # détail KB ("static", "fuzzy", etc.)


_MAX_BUFFER = 200
_MAX_VALUE_LEN = 120


class DashboardEventBus:
    def __init__(self) -> None:
        self._buffer: deque[DetectionEvent] = deque(maxlen=_MAX_BUFFER)
        self._subscribers: list[asyncio.Queue[DetectionEvent]] = []
        self._lock = asyncio.Lock()

    def emit(
        self,
        *,
        token: str,
        value: str,
        source: str,
        label: str,
        confidence: float | None = None,
        field: str | None = None,
        match_type: str | None = None,
    ) -> None:
        # Non-async pour pouvoir être appelé depuis du code sync (anonymizer)
        ev = DetectionEvent(
            ts=time.time(),
            token=token,
            value=value[:_MAX_VALUE_LEN] + ("…" if len(value) > _MAX_VALUE_LEN else ""),
            source=source,
            label=label,
            confidence=confidence,
            field=field,
            match_type=match_type,
        )
        self._buffer.append(ev)
        # Push aux subscribers connectés. Si la queue est saturée → drop.
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    def recent(self, limit: int = 50) -> list[dict]:
        return [asdict(e) for e in list(self._buffer)[-limit:]]

    async def subscribe(self) -> AsyncIterator[DetectionEvent]:
        """Async generator pour SSE. Replay le buffer puis stream le live."""
        queue: asyncio.Queue[DetectionEvent] = asyncio.Queue(maxsize=500)
        async with self._lock:
            self._subscribers.append(queue)
            replay = list(self._buffer)
        try:
            for ev in replay:
                yield ev
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)


def event_to_sse(ev: DetectionEvent) -> str:
    return f"data: {json.dumps(asdict(ev), ensure_ascii=False)}\n\n"

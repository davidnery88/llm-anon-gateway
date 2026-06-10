"""Read-only ColumnLabelStore backed by the KBClient snapshot.

L'interface est duck-typée avec `sidecar.column_labels.ColumnLabelStore` pour que
l'`Anonymizer` ne fasse pas la différence. Les méthodes d'écriture sont des no-ops :
la persistence des classifications qwen est gérée côté gateway (admin UI), pas par
le sidecar. Le sidecar ne fait que CONSOMMER la KB en lecture.

Phase 4.5 — lookup hiérarchique :
(header, db, table) > (header, NULL, table) > (header, db, NULL) > (header, NULL, NULL).
Doctrine d'ambiguïté : si plusieurs labels au même niveau, on prend le plus
sensible (PII > NONE / PUBLIC). Cohérent avec "rather false positive than leak".
"""
from __future__ import annotations

from sidecar.column_labels import normalize_header, fuzzy_lookup
from sidecar.kb_client import KBClient

# Labels considérés comme "non sensibles" — tout le reste est traité comme PII.
_NON_PII_LABELS = frozenset({"NONE", "PUBLIC", "", None})


def _pick_most_sensitive(labels: list[str]) -> str:
    """Doctrine 'rather false positive' : si AU MOINS UN label PII présent,
    on retourne le premier label PII. Sinon le premier label non-PII."""
    pii = [l for l in labels if l not in _NON_PII_LABELS]
    if pii:
        return pii[0]
    return labels[0]


class KBSnapshotLabelStore:
    def __init__(self, kb_client: KBClient, fuzzy_threshold: int = 88):
        self._kb_client = kb_client
        self._threshold = fuzzy_threshold

    async def lookup(
        self,
        header: str,
        db: str | None = None,
        table: str | None = None,
    ) -> tuple[str | None, str]:
        header_norm = normalize_header(header)
        if not header_norm:
            return None, "none"
        snap = self._kb_client.snapshot
        if not snap:
            return None, "none"

        active = [
            r for r in snap.get("column_labels", [])
            if r.get("status", "active") == "active"
        ]

        # 1. Exact header match : applique le lookup hiérarchique par contexte.
        same_header = [r for r in active if r.get("header_norm") == header_norm]
        if same_header:
            # Niveaux de spécificité du plus précis au plus générique.
            # On skip un niveau si le contexte requis n'est pas fourni par l'appelant.
            levels: list[tuple[str, callable]] = []
            if db is not None and table is not None:
                levels.append(("exact", lambda r: r.get("db_name") == db and r.get("table_name") == table))
            if table is not None:
                levels.append(("table", lambda r: r.get("db_name") is None and r.get("table_name") == table))
            if db is not None:
                levels.append(("db", lambda r: r.get("db_name") == db and r.get("table_name") is None))
            # Fallback générique tous-contextes : toujours présent.
            levels.append(("exact", lambda r: r.get("db_name") is None and r.get("table_name") is None))

            for level_name, level_filter in levels:
                matches = [r for r in same_header if level_filter(r)]
                if matches:
                    label = _pick_most_sensitive([r["label"] for r in matches])
                    return label, level_name

        # 2. Fuzzy match sur header_norm (sans contexte — la KB seedée est en
        #    générique, le fuzzy est juste un secours sur faute de frappe / variante).
        all_headers = list({r["header_norm"] for r in active})
        match = fuzzy_lookup(header_norm, all_headers, threshold=self._threshold)
        if match is not None:
            fuzzy_matches = [r for r in active if r["header_norm"] == match]
            if fuzzy_matches:
                label = _pick_most_sensitive([r["label"] for r in fuzzy_matches])
                return label, "fuzzy"

        return None, "none"

    async def upsert(self, header, label, source, confidence=1.0,
                     status="active", sample_values=None) -> dict:
        # No-op : la persistence se fait côté gateway. Le caller (Anonymizer)
        # ignore l'éventuelle exception, donc on retourne juste un dict cohérent.
        return {
            "header_norm": normalize_header(header), "header_raw": header,
            "label": label, "source": source, "status": "ephemeral",
            "confidence": confidence,
        }

    async def increment_occurrence(self, header_norm: str) -> None:
        # No-op : compteurs gérés côté gateway via l'admin UI quand un label
        # est promu active.
        return

from __future__ import annotations

import json
import re
import unicodedata

from rapidfuzz import fuzz

STATIC_HEADER_MAP = {
    # PERSONNE (FR / EN / DE)
    "nom": "PERSONNE", "nom_complet": "PERSONNE", "prenom": "PERSONNE",
    "client": "PERSONNE", "assure": "PERSONNE", "beneficiaire": "PERSONNE",
    "conducteur": "PERSONNE", "preneur": "PERSONNE",
    "name": "PERSONNE", "fullname": "PERSONNE", "full_name": "PERSONNE",
    "first_name": "PERSONNE", "last_name": "PERSONNE", "surname": "PERSONNE",
    "driver": "PERSONNE", "insured": "PERSONNE", "holder": "PERSONNE",
    "vorname": "PERSONNE", "nachname": "PERSONNE",
    "versicherter": "PERSONNE", "fahrer": "PERSONNE", "kunde": "PERSONNE",
    "detenteur": "PERSONNE", "halter": "PERSONNE", "owner": "PERSONNE",
    "vehicle_owner": "PERSONNE", "fahrzeughalter": "PERSONNE",
    # EMAIL
    "email": "EMAIL", "courriel": "EMAIL", "mail": "EMAIL", "e_mail": "EMAIL",
    "mail_address": "EMAIL", "email_address": "EMAIL", "e_mail_adresse": "EMAIL",
    # TEL
    "telephone": "TEL", "tel": "TEL", "mobile": "TEL", "portable": "TEL",
    "phone": "TEL", "phone_number": "TEL", "cellphone": "TEL",
    "telefon": "TEL", "telefonnummer": "TEL", "handy": "TEL", "mobilnummer": "TEL",
    # AVS
    "avs": "AVS", "num_avs": "AVS", "no_avs": "AVS", "n_avs": "AVS",
    "ahv": "AVS", "ahv_nummer": "AVS", "sozialversicherungsnummer": "AVS",
    "ssn": "AVS",
    # LOCALISATION
    "adresse": "LOCALISATION", "address": "LOCALISATION", "rue": "LOCALISATION",
    "ville": "LOCALISATION", "localite": "LOCALISATION", "city": "LOCALISATION",
    "lieu": "LOCALISATION", "domicile": "LOCALISATION",
    "street": "LOCALISATION", "town": "LOCALISATION", "location": "LOCALISATION",
    "place": "LOCALISATION",
    "strasse": "LOCALISATION", "ort": "LOCALISATION", "wohnort": "LOCALISATION",
    "stadt": "LOCALISATION",
    "lieu_incident": "LOCALISATION", "incident_location": "LOCALISATION",
    "ereignisort": "LOCALISATION",
    # DATE
    "date_naissance": "DATE", "naissance": "DATE", "date_sinistre": "DATE",
    "date_incident": "DATE", "date_evenement": "DATE",
    "dob": "DATE", "birthdate": "DATE", "birth_date": "DATE",
    "incident_date": "DATE", "event_date": "DATE",
    "geburtsdatum": "DATE", "schadendatum": "DATE", "ereignisdatum": "DATE",
    # POLICE / CONTRAT
    "num_police": "POLICE", "police": "POLICE", "n_police": "POLICE",
    "policy": "POLICE", "policy_number": "POLICE", "policy_no": "POLICE",
    "policennummer": "POLICE", "police_nr": "POLICE",
    "num_contrat": "CONTRAT", "contrat": "CONTRAT", "n_contrat": "CONTRAT",
    "contract": "CONTRAT", "contract_number": "CONTRAT", "contract_no": "CONTRAT",
    "vertrag": "CONTRAT", "vertragsnummer": "CONTRAT",
    # SINISTRE / INTERVENTION
    "num_sinistre": "REF_SINISTRE", "sinistre": "REF_SINISTRE",
    "no_sinistre": "REF_SINISTRE", "dossier": "REF_SINISTRE",
    "claim": "REF_SINISTRE", "claim_number": "REF_SINISTRE", "case": "REF_SINISTRE",
    "schadenfall": "REF_SINISTRE", "schadennummer": "REF_SINISTRE",
    "fallnummer": "REF_SINISTRE",
    "intervention": "REF_SINISTRE", "intervention_id": "REF_SINISTRE",
    "einsatz": "REF_SINISTRE", "einsatznummer": "REF_SINISTRE",
    # IBAN
    "iban": "IBAN", "compte": "IBAN", "rib": "IBAN",
    "account": "IBAN", "bank_account": "IBAN",
    "konto": "IBAN", "kontonummer": "IBAN", "bankverbindung": "IBAN",
    # PLAQUE
    "plaque": "PLAQUE", "immatriculation": "PLAQUE", "no_immat": "PLAQUE",
    "plate": "PLAQUE", "license_plate": "PLAQUE", "registration": "PLAQUE",
    "kennzeichen": "PLAQUE", "nummernschild": "PLAQUE",
    # VIN / CHASSIS
    "chassis": "VIN", "no_chassis": "VIN", "numero_chassis": "VIN",
    "vin": "VIN", "chassis_number": "VIN", "chassis_no": "VIN",
    "fahrgestellnummer": "VIN", "fahrgestell_nr": "VIN", "fin": "VIN",
    # PERMIS
    "permis_circulation": "PERMIS", "no_permis": "PERMIS",
    "numero_permis_circulation": "PERMIS",
    "registration_number": "PERMIS", "registration_no": "PERMIS",
    "fahrzeugausweis": "PERMIS", "fahrzeugausweisnummer": "PERMIS",
}

_SEP_CHARS = {" ", "-", ".", "/", "\t"}
_MULTI_US = re.compile(r"_+")
_CACHE_PREFIX = "column_label:"
_CACHE_TTL = 3600  # 1h
_NULL_SENTINEL = b"__null__"


def normalize_header(s: str) -> str:
    if s is None:
        return ""
    s = s.lower()
    # NFD + strip combining marks
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # replace separators with underscore
    s = "".join("_" if ch in _SEP_CHARS else ch for ch in s)
    # collapse multiple underscores
    s = _MULTI_US.sub("_", s)
    # strip leading/trailing underscores
    return s.strip("_")


def fuzzy_lookup(
    header_norm: str,
    known_headers: list[str],
    threshold: int = 88,
) -> str | None:
    if not header_norm or not known_headers:
        return None

    scored: list[tuple[str, float]] = []
    for kh in known_headers:
        score = fuzz.token_set_ratio(header_norm, kh)
        scored.append((kh, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    best_header, best_score = scored[0]

    if best_score < threshold:
        return None

    # ambiguity check: anything within 3 points of best, above threshold,
    # that isn't the best itself => ambiguous
    for kh, score in scored[1:]:
        if score >= threshold and (best_score - score) <= 3:
            return None
        break  # list is sorted; first non-best is the runner-up

    return best_header


class ColumnLabelStore:
    def __init__(self, pool, redis_client, fuzzy_threshold: int = 88):
        self._pool = pool
        self._redis = redis_client
        self._threshold = fuzzy_threshold

    def _cache_key(self, header_norm: str) -> str:
        return f"{_CACHE_PREFIX}{header_norm}"

    async def _invalidate(self, header_norm: str) -> None:
        try:
            await self._redis.delete(self._cache_key(header_norm))
        except Exception:
            pass

    async def lookup(self, header: str) -> tuple[str | None, str]:
        header_norm = normalize_header(header)
        if not header_norm:
            return None, "none"

        cache_key = self._cache_key(header_norm)
        try:
            cached = await self._redis.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            if cached == _NULL_SENTINEL:
                return None, "none"
            return cached.decode(), "exact"

        # Exact match in DB (status='active')
        row = await self._pool.fetchrow(
            "SELECT label FROM column_labels WHERE header_norm = $1 AND status = 'active'",
            header_norm,
        )
        if row is not None:
            label = row["label"]
            try:
                await self._redis.set(cache_key, label, ex=_CACHE_TTL)
            except Exception:
                pass
            return label, "exact"

        # Fuzzy match against all active known headers
        rows = await self._pool.fetch(
            "SELECT header_norm, label FROM column_labels WHERE status = 'active'"
        )
        known = [r["header_norm"] for r in rows]
        match = fuzzy_lookup(header_norm, known, threshold=self._threshold)
        if match is not None:
            label = next(r["label"] for r in rows if r["header_norm"] == match)
            try:
                await self._redis.set(cache_key, label, ex=_CACHE_TTL)
            except Exception:
                pass
            return label, "fuzzy"

        # Negative cache
        try:
            await self._redis.set(cache_key, _NULL_SENTINEL, ex=_CACHE_TTL)
        except Exception:
            pass
        return None, "none"

    async def upsert(
        self,
        header: str,
        label: str,
        source: str,
        confidence: float = 1.0,
        status: str = "active",
        sample_values: list[str] | None = None,
    ) -> dict:
        header_norm = normalize_header(header)
        sample_json = json.dumps(sample_values, ensure_ascii=False) if sample_values is not None else None
        row = await self._pool.fetchrow(
            """
            INSERT INTO column_labels
                (header_norm, header_raw, label, source, status, confidence, sample_values)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (header_norm) DO UPDATE
                SET header_raw = EXCLUDED.header_raw,
                    label = EXCLUDED.label,
                    source = EXCLUDED.source,
                    status = EXCLUDED.status,
                    confidence = EXCLUDED.confidence,
                    sample_values = COALESCE(EXCLUDED.sample_values, column_labels.sample_values),
                    updated_at = NOW()
            RETURNING id, header_norm, header_raw, label, source, status,
                      confidence, occurrences, sample_values, created_at, updated_at
            """,
            header_norm, header, label, source, status, confidence, sample_json,
        )
        await self._invalidate(header_norm)
        return dict(row)

    async def increment_occurrence(self, header_norm: str) -> None:
        await self._pool.execute(
            "UPDATE column_labels SET occurrences = occurrences + 1, updated_at = NOW() "
            "WHERE header_norm = $1",
            header_norm,
        )

    async def list_all(self, status: str | None = None) -> list[dict]:
        if status is None:
            rows = await self._pool.fetch(
                "SELECT id, header_norm, header_raw, label, source, status, "
                "confidence, occurrences, sample_values, created_at, updated_at "
                "FROM column_labels ORDER BY occurrences DESC"
            )
        else:
            rows = await self._pool.fetch(
                "SELECT id, header_norm, header_raw, label, source, status, "
                "confidence, occurrences, sample_values, created_at, updated_at "
                "FROM column_labels WHERE status = $1 ORDER BY occurrences DESC",
                status,
            )
        return [dict(r) for r in rows]

    async def approve(self, label_id: int) -> dict:
        row = await self._pool.fetchrow(
            "UPDATE column_labels SET status = 'active', source = 'admin', updated_at = NOW() "
            "WHERE id = $1 "
            "RETURNING id, header_norm, header_raw, label, source, status, "
            "confidence, occurrences, sample_values, created_at, updated_at",
            label_id,
        )
        if row is not None:
            await self._invalidate(row["header_norm"])
        return dict(row) if row else {}

    async def reject(self, label_id: int) -> None:
        row = await self._pool.fetchrow(
            "DELETE FROM column_labels WHERE id = $1 RETURNING header_norm",
            label_id,
        )
        if row is not None:
            await self._invalidate(row["header_norm"])

    async def update(self, label_id: int, label: str, source: str = "admin") -> dict:
        row = await self._pool.fetchrow(
            "UPDATE column_labels SET label = $2, source = $3, updated_at = NOW() "
            "WHERE id = $1 "
            "RETURNING id, header_norm, header_raw, label, source, status, "
            "confidence, occurrences, sample_values, created_at, updated_at",
            label_id, label, source,
        )
        if row is not None:
            await self._invalidate(row["header_norm"])
        return dict(row) if row else {}

    async def delete(self, label_id: int) -> None:
        row = await self._pool.fetchrow(
            "DELETE FROM column_labels WHERE id = $1 RETURNING header_norm",
            label_id,
        )
        if row is not None:
            await self._invalidate(row["header_norm"])

    async def bulk_approve(self, label_ids: list[int]) -> int:
        if not label_ids:
            return 0
        rows = await self._pool.fetch(
            "UPDATE column_labels SET status = 'active', source = 'admin', updated_at = NOW() "
            "WHERE id = ANY($1::int[]) "
            "RETURNING header_norm",
            label_ids,
        )
        for r in rows:
            await self._invalidate(r["header_norm"])
        return len(rows)


async def seed_static_map(pool) -> int:
    """Seed STATIC_HEADER_MAP into column_labels. Idempotent.

    Returns the number of inserted rows.
    """
    inserted = 0
    for header_norm, label in STATIC_HEADER_MAP.items():
        result = await pool.execute(
            """
            INSERT INTO column_labels
                (header_norm, header_raw, label, source, status, confidence)
            VALUES ($1, $2, $3, 'static', 'active', 1.0)
            ON CONFLICT (header_norm) DO NOTHING
            """,
            header_norm, header_norm, label,
        )
        # asyncpg returns 'INSERT 0 1' on insert, 'INSERT 0 0' when skipped
        if isinstance(result, str) and result.endswith(" 1"):
            inserted += 1
    return inserted

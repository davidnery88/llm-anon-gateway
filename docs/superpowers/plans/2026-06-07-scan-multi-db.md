# Scan multi-DB depuis l'admin UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettre à un admin de configurer N serveurs DB et de lancer un scan batch qui classe les colonnes (tables + vues) via qwen3-pii et peuple `column_labels` en `pending`.

**Architecture:** Tout côté gateway (FastAPI + asyncpg). Connecteurs multi-moteur via SQLAlchemy Core (sync, appelés en `asyncio.to_thread`). Scan en tâche background avec suivi de job. Réutilise `ColumnClassifier` (qwen, métadonnées only) + `column_labels` (pending/bulk_approve) + la section « À valider » du frontend.

**Tech Stack:** FastAPI, asyncpg, SQLAlchemy Core, psycopg2-binary/pymysql/pyodbc/sqlite3, cryptography (Fernet), pytest.

**Spec:** `docs/superpowers/specs/2026-06-07-scan-multi-db-design.md`

**Conventions du repo :** tests dans `gateway/tests/`, lancés via `cd gateway && python -m pytest`. Les modules accèdent au pool asyncpg via `app.state.db_pool` ; les routers suivent le pattern de `gateway/admin_router.py` (`_require_admin` + `request.app.state.*`).

---

## File Structure

- Create `gateway/value_metadata.py` — calcul des métadonnées d'une valeur (repris du sidecar).
- Create `gateway/dwh_sources.py` — Fernet encrypt/decrypt + `DwhSourceStore` (sources + scan_jobs, asyncpg).
- Create `gateway/db_connectors.py` — `get_connector(source)` → `list_databases / list_objects / sample_values` (SQLAlchemy).
- Create `gateway/scanner.py` — `run_scan(...)` (logique pure, dépendances injectées → testable).
- Create `gateway/dwh_router.py` — endpoints `/admin/dwh_sources/*`.
- Modify `gateway/column_labels.py` — ajouter `upsert_ctx()` + `exists_active()`.
- Modify `gateway/main.py` — wirer `DwhSourceStore` dans `app.state` + `include_router(dwh_router)`.
- Modify `scripts/init_db.sql` — tables `dwh_sources` + `scan_jobs`.
- Modify `gateway/requirements.txt` — deps.
- Modify `gateway/Dockerfile` — ODBC + msodbcsql18.
- Modify `frontend/admin.html` — section « Sources de données ».
- Modify `docs/SECURITY.md` + `ROADMAP.md` — documenter le scan gateway-side.

---

## Task 1 : Schéma DB (tables dwh_sources + scan_jobs)

**Files:**
- Modify: `scripts/init_db.sql` (ajouter à la fin)

- [ ] **Step 1: Ajouter les deux tables**

Ajouter à la fin de `scripts/init_db.sql` :
```sql
CREATE TABLE IF NOT EXISTS dwh_sources (
    id                 SERIAL PRIMARY KEY,
    name               VARCHAR(64) NOT NULL UNIQUE,
    db_type            VARCHAR(16) NOT NULL,
    host               VARCHAR(255),
    port               INTEGER,
    username           VARCHAR(128),
    password_encrypted TEXT,
    options            JSONB NOT NULL DEFAULT '{}',
    db_filter          TEXT[] NOT NULL DEFAULT '{}',
    last_scan_at       TIMESTAMPTZ,
    last_scan_status   VARCHAR(16),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scan_jobs (
    id            SERIAL PRIMARY KEY,
    source_id     INTEGER REFERENCES dwh_sources(id) ON DELETE CASCADE,
    status        VARCHAR(16) NOT NULL DEFAULT 'running',
    total_cols    INTEGER NOT NULL DEFAULT 0,
    scanned_cols  INTEGER NOT NULL DEFAULT 0,
    found_labels  INTEGER NOT NULL DEFAULT 0,
    current_db    VARCHAR(64),
    current_table VARCHAR(128),
    error         TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS scan_jobs_source_idx ON scan_jobs(source_id);
```

- [ ] **Step 2: Commit**
```bash
git add scripts/init_db.sql
git commit -m "feat(db): tables dwh_sources et scan_jobs pour le scan multi-DB"
```

---

## Task 2 : Module value_metadata (repris du sidecar)

**Files:**
- Create: `gateway/value_metadata.py`
- Test: `gateway/tests/test_value_metadata.py`

- [ ] **Step 1: Écrire le test (échoue)**
```python
# gateway/tests/test_value_metadata.py
from gateway.value_metadata import value_metadata

def test_email():
    m = value_metadata("a.b@example.ch")
    assert m["charset"] == "email"
    assert m["length"] == len("a.b@example.ch")
    assert "sample_hash" in m and len(m["sample_hash"]) == 8

def test_avs_regex_hint():
    assert value_metadata("7561234567890")["regex_hint"] == "avs"

def test_iban_regex_hint():
    assert value_metadata("CH9300762011623852957")["regex_hint"] == "iban_ch"

def test_plain_digits_no_hint():
    assert value_metadata("4471234")["regex_hint"] == "none"
    assert value_metadata("4471234")["charset"] == "digits"
```

- [ ] **Step 2: Lancer → FAIL**
Run: `cd gateway && python -m pytest tests/test_value_metadata.py -v`
Expected: FAIL (ModuleNotFoundError: gateway.value_metadata)

- [ ] **Step 3: Implémenter (copie de la logique sidecar)**
```python
# gateway/value_metadata.py
"""Calcul des métadonnées d'une valeur (longueur, charset, regex_hint, hash).

Repris VERBATIM de sidecar/column_classifier.py : le gateway scanne lui-même les
DB (mode gateway-side) et doit produire les MÊMES métadonnées que le sidecar pour
que le classeur qwen3-pii reçoive le format attendu.
"""
from __future__ import annotations
import hashlib
import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_DIGITS_RE = re.compile(r"^[0-9]+$")
_ALPHA_RE = re.compile(r"^[a-zA-Z]+$")
_ALPHANUM_RE = re.compile(r"^[a-zA-Z0-9]+$")

_REGEX_HINTS = [
    (re.compile(r"^[0-9]{13}$"), "avs"),
    (re.compile(r"^(CH|LI)[0-9]{2}[0-9]{5}[A-Za-z0-9]{5,17}$"), "iban_ch"),
    (re.compile(r"^(\+41|0041)?[0-9]{2,3}\s?[0-9]{3}\s?[0-9]{2}\s?[0-9]{2}$"), "phone"),
    (re.compile(r"^[0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4}$"), "date"),
    (re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"), "date"),
    (re.compile(r"^[0-9]{2}:[0-9]{2}(:[0-9]{2})?$"), "date"),
]


def _charset(v: str) -> str:
    if _EMAIL_RE.match(v): return "email"
    if _URL_RE.match(v): return "url"
    if _DIGITS_RE.match(v): return "digits"
    if _ALPHA_RE.match(v): return "alpha"
    if _ALPHANUM_RE.match(v): return "alphanum"
    return "mixed"


def _regex_hint(v: str) -> str:
    for pat, hint in _REGEX_HINTS:
        if pat.match(v):
            return hint
    return "none"


def value_metadata(value: str) -> dict:
    value = str(value)
    return {
        "length": len(value),
        "charset": _charset(value),
        "has_spaces": " " in value,
        "has_punctuation": bool(re.search(r"[.,;:!?'\-/@&]", value)),
        "regex_hint": _regex_hint(value),
        "sample_hash": hashlib.sha256(value.encode("utf-8")).hexdigest()[:8],
    }
```

- [ ] **Step 4: Lancer → PASS**
Run: `cd gateway && python -m pytest tests/test_value_metadata.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**
```bash
git add gateway/value_metadata.py gateway/tests/test_value_metadata.py
git commit -m "feat(gateway): module value_metadata (métadonnées pour le scan)"
```

---

## Task 3 : Chiffrement Fernet des credentials

**Files:**
- Create: `gateway/dwh_sources.py` (partie crypto seulement pour ce task)
- Test: `gateway/tests/test_dwh_crypto.py`

- [ ] **Step 1: Écrire le test (échoue)**
```python
# gateway/tests/test_dwh_crypto.py
import os
import pytest
from cryptography.fernet import Fernet

def test_roundtrip(monkeypatch):
    monkeypatch.setenv("DWH_ENC_KEY", Fernet.generate_key().decode())
    from importlib import reload
    import gateway.dwh_sources as m; reload(m)
    token = m.encrypt_secret("hunter2")
    assert token != "hunter2"
    assert m.decrypt_secret(token) == "hunter2"

def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("DWH_ENC_KEY", raising=False)
    from importlib import reload
    import gateway.dwh_sources as m; reload(m)
    with pytest.raises(RuntimeError):
        m.encrypt_secret("x")
```

- [ ] **Step 2: Lancer → FAIL**
Run: `cd gateway && python -m pytest tests/test_dwh_crypto.py -v`
Expected: FAIL (no encrypt_secret)

- [ ] **Step 3: Implémenter le bloc crypto**
```python
# gateway/dwh_sources.py
"""Sources DWH : chiffrement des credentials + persistance (sources + scan_jobs)."""
from __future__ import annotations
import json
import os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get("DWH_ENC_KEY")
    if not key:
        raise RuntimeError("DWH_ENC_KEY manquant : impossible de (dé)chiffrer les credentials DWH")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
```

- [ ] **Step 4: Lancer → PASS**
Run: `cd gateway && python -m pytest tests/test_dwh_crypto.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**
```bash
git add gateway/dwh_sources.py gateway/tests/test_dwh_crypto.py
git commit -m "feat(gateway): chiffrement Fernet des credentials DWH"
```

---

## Task 4 : DwhSourceStore (persistance sources + scan_jobs)

**Files:**
- Modify: `gateway/dwh_sources.py` (ajouter la classe)
- Test: `gateway/tests/test_dwh_store.py`

**Note pour l'engineer :** ces tests nécessitent le pool Postgres de test utilisé par les autres tests gateway (voir `gateway/tests/conftest.py` / `test_admin.py` pour la fixture `db_pool`). Réutiliser la même fixture.

- [ ] **Step 1: Écrire le test (échoue)**
```python
# gateway/tests/test_dwh_store.py
import os, pytest
from cryptography.fernet import Fernet

@pytest.mark.asyncio
async def test_create_list_get(db_pool, monkeypatch):
    monkeypatch.setenv("DWH_ENC_KEY", Fernet.generate_key().decode())
    from gateway.dwh_sources import DwhSourceStore
    store = DwhSourceStore(db_pool)
    src = await store.create(name="dwh1", db_type="sqlite", host=None, port=None,
                             username=None, password="secret", options={"sqlite_path": "/tmp/x.db"},
                             db_filter=[])
    # list() ne renvoie jamais le mot de passe
    listed = await store.list()
    assert any(s["name"] == "dwh1" for s in listed)
    assert all("password_encrypted" not in s and "password" not in s for s in listed)
    # get() interne renvoie le password déchiffrable
    full = await store.get(src["id"])
    from gateway.dwh_sources import decrypt_secret
    assert decrypt_secret(full["password_encrypted"]) == "secret"
    await store.delete(src["id"])

@pytest.mark.asyncio
async def test_job_lifecycle(db_pool):
    from gateway.dwh_sources import DwhSourceStore
    store = DwhSourceStore(db_pool)
    src = await store.create(name="dwh2", db_type="sqlite", host=None, port=None,
                             username=None, password=None, options={}, db_filter=[])
    job = await store.create_job(src["id"])
    await store.update_job(job["id"], scanned_cols=3, total_cols=10, current_table="t")
    await store.finish_job(job["id"], status="done")
    got = await store.get_job(job["id"])
    assert got["status"] == "done" and got["scanned_cols"] == 3
    await store.delete(src["id"])
```

- [ ] **Step 2: Lancer → FAIL**
Run: `cd gateway && python -m pytest tests/test_dwh_store.py -v`
Expected: FAIL (no DwhSourceStore)

- [ ] **Step 3: Implémenter la classe (ajouter à `gateway/dwh_sources.py`)**
```python
class DwhSourceStore:
    def __init__(self, pool):
        self._pool = pool

    async def create(self, *, name, db_type, host, port, username, password,
                     options: dict, db_filter: list[str]) -> dict:
        pw = encrypt_secret(password) if password else None
        row = await self._pool.fetchrow(
            """INSERT INTO dwh_sources
               (name, db_type, host, port, username, password_encrypted, options, db_filter)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               RETURNING id, name, db_type, host, port, username, options, db_filter,
                         last_scan_at, last_scan_status, created_at""",
            name, db_type, host, port, username, pw, json.dumps(options), db_filter,
        )
        return dict(row)

    async def list(self) -> list[dict]:
        rows = await self._pool.fetch(
            """SELECT id, name, db_type, host, port, username, options, db_filter,
                      last_scan_at, last_scan_status, created_at
               FROM dwh_sources ORDER BY name""")
        return [dict(r) for r in rows]

    async def get(self, source_id: int) -> dict | None:
        row = await self._pool.fetchrow("SELECT * FROM dwh_sources WHERE id=$1", source_id)
        return dict(row) if row else None

    async def update(self, source_id: int, *, name, db_type, host, port, username,
                     password, options: dict, db_filter: list[str]) -> dict:
        # password=None => on garde l'existant (write-only)
        if password:
            pw = encrypt_secret(password)
            set_pw = ", password_encrypted=$6"
        else:
            pw = None
            set_pw = ""
        row = await self._pool.fetchrow(
            f"""UPDATE dwh_sources SET name=$2, db_type=$3, host=$4, port=$5, username=$7,
                   options=$8, db_filter=$9{set_pw}
                WHERE id=$1
                RETURNING id, name, db_type, host, port, username, options, db_filter,
                          last_scan_at, last_scan_status, created_at""",
            source_id, name, db_type, host, port, pw, username, json.dumps(options), db_filter,
        )
        return dict(row)

    async def delete(self, source_id: int) -> None:
        await self._pool.execute("DELETE FROM dwh_sources WHERE id=$1", source_id)

    async def set_last_scan(self, source_id: int, status: str) -> None:
        await self._pool.execute(
            "UPDATE dwh_sources SET last_scan_at=NOW(), last_scan_status=$2 WHERE id=$1",
            source_id, status)

    # --- scan_jobs ---
    async def create_job(self, source_id: int) -> dict:
        row = await self._pool.fetchrow(
            "INSERT INTO scan_jobs (source_id) VALUES ($1) RETURNING *", source_id)
        return dict(row)

    async def update_job(self, job_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=${i+2}" for i, k in enumerate(fields))
        await self._pool.execute(
            f"UPDATE scan_jobs SET {cols} WHERE id=$1", job_id, *fields.values())

    async def finish_job(self, job_id: int, status: str, error: str | None = None) -> None:
        await self._pool.execute(
            "UPDATE scan_jobs SET status=$2, error=$3, finished_at=NOW() WHERE id=$1",
            job_id, status, error)

    async def get_job(self, job_id: int) -> dict | None:
        row = await self._pool.fetchrow("SELECT * FROM scan_jobs WHERE id=$1", job_id)
        return dict(row) if row else None
```

Note : `options` revient en JSON string depuis asyncpg ; le caller le parse avec `json.loads` si besoin (les connecteurs le font).

- [ ] **Step 4: Lancer → PASS**
Run: `cd gateway && python -m pytest tests/test_dwh_store.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**
```bash
git add gateway/dwh_sources.py gateway/tests/test_dwh_store.py
git commit -m "feat(gateway): DwhSourceStore (CRUD sources + scan_jobs)"
```

---

## Task 5 : Connecteurs DB (SQLAlchemy, SQLite d'abord)

**Files:**
- Create: `gateway/db_connectors.py`
- Test: `gateway/tests/test_db_connectors.py`

- [ ] **Step 1: Écrire le test (échoue) — SQLite réel**
```python
# gateway/tests/test_db_connectors.py
import sqlite3, tempfile, os
from gateway.db_connectors import get_connector

def _make_sqlite():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clients (nom TEXT, email TEXT)")
    con.execute("INSERT INTO clients VALUES ('David Dupont','d@x.ch'),('Anna','a@y.ch')")
    con.execute("CREATE VIEW v_clients AS SELECT nom FROM clients")
    con.commit(); con.close()
    return path

def test_sqlite_list_objects_and_sample():
    path = _make_sqlite()
    conn = get_connector({"db_type": "sqlite", "options": {"sqlite_path": path}})
    cols = conn.list_objects(db="main", include_views=True)
    names = {(c.table, c.column) for c in cols}
    assert ("clients", "nom") in names
    assert ("clients", "email") in names
    assert ("v_clients", "nom") in names  # vue incluse
    ref = next(c for c in cols if c.table == "clients" and c.column == "nom")
    vals = conn.sample_values(ref, n=5)
    assert "David Dupont" in vals

def test_sqlite_exclude_views():
    path = _make_sqlite()
    conn = get_connector({"db_type": "sqlite", "options": {"sqlite_path": path}})
    cols = conn.list_objects(db="main", include_views=False)
    assert all(c.table != "v_clients" for c in cols)
```

- [ ] **Step 2: Lancer → FAIL**
Run: `cd gateway && python -m pytest tests/test_db_connectors.py -v`
Expected: FAIL (no get_connector)

- [ ] **Step 3: Implémenter**
```python
# gateway/db_connectors.py
"""Connecteurs multi-moteur via SQLAlchemy Core (sync). Appeler depuis asyncio.to_thread."""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

_SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "sys",
                   "mysql", "performance_schema", "INFORMATION_SCHEMA"}


@dataclass
class ColumnRef:
    db: str
    schema: str | None
    table: str
    object_type: str  # "table" | "view"
    column: str
    sql_type: str


def _build_url(source: dict, db: str | None) -> URL | str:
    t = source["db_type"]
    opts = source.get("options") or {}
    if t == "sqlite":
        return f"sqlite:///{opts['sqlite_path']}"
    drivers = {"postgresql": "postgresql+psycopg2",
               "mysql": "mysql+pymysql",
               "sqlserver": "mssql+pyodbc"}
    query = {}
    if t == "sqlserver":
        query["driver"] = opts.get("odbc_driver", "ODBC Driver 18 for SQL Server")
        query["TrustServerCertificate"] = "yes"
    return URL.create(drivers[t], username=source.get("username"),
                      password=source.get("_password"), host=source.get("host"),
                      port=source.get("port"), database=db, query=query)


class Connector:
    def __init__(self, source: dict):
        self.source = source
        self.db_type = source["db_type"]

    def list_databases(self) -> list[str]:
        if self.db_type == "sqlite":
            return ["main"]
        eng = create_engine(_build_url(self.source, None))
        try:
            with eng.connect() as c:
                if self.db_type == "postgresql":
                    rows = c.execute(text("SELECT datname FROM pg_database WHERE datistemplate=false"))
                elif self.db_type == "mysql":
                    rows = c.execute(text("SHOW DATABASES"))
                else:  # sqlserver
                    rows = c.execute(text("SELECT name FROM sys.databases WHERE database_id>4"))
                return [r[0] for r in rows if r[0] not in _SYSTEM_SCHEMAS]
        finally:
            eng.dispose()

    def list_objects(self, db: str, include_views: bool = True) -> list[ColumnRef]:
        eng = create_engine(_build_url(self.source, None if self.db_type == "sqlite" else db))
        out: list[ColumnRef] = []
        try:
            insp = inspect(eng)
            schemas = [None] if self.db_type in ("sqlite", "mysql") else insp.get_schema_names()
            for schema in schemas:
                if schema in _SYSTEM_SCHEMAS:
                    continue
                tables = [(t, "table") for t in insp.get_table_names(schema=schema)]
                if include_views:
                    tables += [(v, "view") for v in insp.get_view_names(schema=schema)]
                for tname, otype in tables:
                    for col in insp.get_columns(tname, schema=schema):
                        out.append(ColumnRef(db=db, schema=schema, table=tname,
                                             object_type=otype, column=col["name"],
                                             sql_type=str(col["type"])))
        finally:
            eng.dispose()
        return out

    def sample_values(self, ref: ColumnRef, n: int = 5) -> list[str]:
        url = _build_url(self.source, None if self.db_type == "sqlite" else ref.db)
        eng = create_engine(url)
        q = f'SELECT "{ref.column}" FROM "{ref.table}"'
        if self.db_type == "sqlserver":
            q = f'SELECT TOP {n} [{ref.column}] FROM [{ref.table}]'
        else:
            q += f" LIMIT {n}"
        try:
            with eng.connect() as c:
                rows = c.execute(text(q))
                return [str(r[0]) for r in rows if r[0] is not None][:n]
        finally:
            eng.dispose()


def get_connector(source: dict) -> Connector:
    return Connector(source)
```

Note : `_build_url` lit `source["_password"]` (le mot de passe **déjà déchiffré**, injecté par le scanner — voir Task 6) ; la source persistée ne contient que `password_encrypted`.

- [ ] **Step 4: Lancer → PASS**
Run: `cd gateway && python -m pytest tests/test_db_connectors.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**
```bash
git add gateway/db_connectors.py gateway/tests/test_db_connectors.py
git commit -m "feat(gateway): connecteurs DB multi-moteur (SQLAlchemy), tables+vues"
```

---

## Task 6 : column_labels — upsert_ctx + exists_active

**Files:**
- Modify: `gateway/column_labels.py` (ajouter 2 méthodes à `ColumnLabelStore`)
- Test: `gateway/tests/test_column_labels_ctx.py`

- [ ] **Step 1: Écrire le test (échoue)**
```python
# gateway/tests/test_column_labels_ctx.py
import pytest
from gateway.column_labels import ColumnLabelStore

@pytest.mark.asyncio
async def test_upsert_ctx_and_exists_active(db_pool):
    store = ColumnLabelStore(db_pool, redis_client=None)
    await store.upsert_ctx(header="ref_contrat", label="ID", source="qwen3",
                           confidence=0.9, status="active",
                           db_name="dwh", table_name="contrats", sample_values=["A","B"])
    assert await store.exists_active("ref_contrat", "dwh", "contrats") is True
    # autre table => pas encore actif
    assert await store.exists_active("ref_contrat", "dwh", "sinistres") is False
    # pending ne compte pas comme actif
    await store.upsert_ctx(header="x_pending", label="ID", source="qwen3",
                           confidence=0.3, status="pending", db_name="dwh", table_name="t")
    assert await store.exists_active("x_pending", "dwh", "t") is False
```

- [ ] **Step 2: Lancer → FAIL**
Run: `cd gateway && python -m pytest tests/test_column_labels_ctx.py -v`
Expected: FAIL (no upsert_ctx)

- [ ] **Step 3: Implémenter (ajouter dans `ColumnLabelStore`, après `upsert`)**
```python
    async def upsert_ctx(self, header: str, label: str, source: str,
                         confidence: float = 1.0, status: str = "active",
                         db_name: str | None = None, table_name: str | None = None,
                         sample_values: list[str] | None = None) -> dict:
        """Comme upsert mais avec contexte (db_name, table_name) — pour le scan multi-DB."""
        import json as _json
        header_norm = normalize_header(header)
        sample_json = _json.dumps(sample_values, ensure_ascii=False) if sample_values is not None else None
        row = await self._pool.fetchrow(
            """
            INSERT INTO column_labels
                (header_norm, header_raw, label, source, status, confidence, sample_values, db_name, table_name)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (header_norm, db_name, table_name) DO UPDATE
                SET header_raw=EXCLUDED.header_raw, label=EXCLUDED.label, source=EXCLUDED.source,
                    status=EXCLUDED.status, confidence=EXCLUDED.confidence,
                    sample_values=COALESCE(EXCLUDED.sample_values, column_labels.sample_values),
                    updated_at=NOW()
            RETURNING id, header_norm, label, source, status, db_name, table_name
            """,
            header_norm, header, label, source, status, confidence, sample_json, db_name, table_name,
        )
        await self._invalidate(header_norm)
        return dict(row)

    async def exists_active(self, header: str, db_name: str | None, table_name: str | None) -> bool:
        header_norm = normalize_header(header)
        row = await self._pool.fetchrow(
            "SELECT 1 FROM column_labels WHERE header_norm=$1 AND db_name IS NOT DISTINCT FROM $2 "
            "AND table_name IS NOT DISTINCT FROM $3 AND status='active'",
            header_norm, db_name, table_name)
        return row is not None
```

- [ ] **Step 4: Lancer → PASS**
Run: `cd gateway && python -m pytest tests/test_column_labels_ctx.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add gateway/column_labels.py gateway/tests/test_column_labels_ctx.py
git commit -m "feat(gateway): column_labels.upsert_ctx + exists_active (scan contextuel)"
```

---

## Task 7 : Moteur de scan (run_scan, dépendances injectées)

**Files:**
- Create: `gateway/scanner.py`
- Test: `gateway/tests/test_scanner.py`

Conception : `run_scan` reçoit ses dépendances (connector, classifier, labels_store, job_store) → testable avec des fakes + un connecteur SQLite réel + un classeur mocké. Pas besoin de Postgres pour ce test (fakes en mémoire).

- [ ] **Step 1: Écrire le test (échoue)**
```python
# gateway/tests/test_scanner.py
import sqlite3, tempfile, os, pytest
from gateway.db_connectors import get_connector
from gateway.scanner import run_scan

class FakeLabels:
    def __init__(self): self.rows = []; self.active = set()
    async def exists_active(self, h, db, t): return (h, db, t) in self.active
    async def upsert_ctx(self, header, label, source, confidence, status, db_name, table_name, sample_values=None):
        self.rows.append((header, label, status, db_name, table_name))
        if status == "active": self.active.add((header, db_name, table_name))
        return {}

class FakeJobs:
    def __init__(self): self.updates = []; self.finished = None
    async def update_job(self, job_id, **f): self.updates.append(f)
    async def finish_job(self, job_id, status, error=None): self.finished = (status, error)

class FakeClassifier:
    async def classify(self, table, column, sql_type, values=None, value_metadata=None):
        return ("EMAIL" if column == "email" else "PERSONNE", 0.95)

def _sqlite():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE clients (nom TEXT, email TEXT)")
    con.execute("INSERT INTO clients VALUES ('David','d@x.ch')")
    con.commit(); con.close(); return p

@pytest.mark.asyncio
async def test_run_scan_populates_labels():
    path = _sqlite()
    conn = get_connector({"db_type": "sqlite", "options": {"sqlite_path": path}})
    labels, jobs = FakeLabels(), FakeJobs()
    await run_scan(connector=conn, classifier=FakeClassifier(), labels=labels, jobs=jobs,
                   job_id=1, dbs=["main"], include_views=True, threshold=0.7)
    cols = {r[0] for r in labels.rows}
    assert "nom" in cols and "email" in cols
    assert jobs.finished == ("done", None)

@pytest.mark.asyncio
async def test_run_scan_skips_active():
    path = _sqlite()
    conn = get_connector({"db_type": "sqlite", "options": {"sqlite_path": path}})
    labels, jobs = FakeLabels(), FakeJobs()
    labels.active.add(("nom", "main", "clients"))  # déjà actif
    await run_scan(connector=conn, classifier=FakeClassifier(), labels=labels, jobs=jobs,
                   job_id=1, dbs=["main"], include_views=True, threshold=0.7)
    assert all(r[0] != "nom" for r in labels.rows)  # nom sauté
```

- [ ] **Step 2: Lancer → FAIL**
Run: `cd gateway && python -m pytest tests/test_scanner.py -v`
Expected: FAIL (no scanner)

- [ ] **Step 3: Implémenter**
```python
# gateway/scanner.py
"""Moteur de scan multi-DB. run_scan() = logique pure, dépendances injectées."""
from __future__ import annotations
import asyncio
from gateway.value_metadata import value_metadata
from gateway.logging_config import get as _log

_logger = _log("gateway.scanner")


async def run_scan(*, connector, classifier, labels, jobs, job_id: int,
                   dbs: list[str], include_views: bool, threshold: float,
                   sample_n: int = 5) -> None:
    try:
        refs = []
        for db in dbs:
            refs += await asyncio.to_thread(connector.list_objects, db, include_views)
        await jobs.update_job(job_id, total_cols=len(refs))
        scanned = found = 0
        for ref in refs:
            job = await jobs.get_job(job_id) if hasattr(jobs, "get_job") else None
            if job and job.get("status") == "cancelled":
                return
            scanned += 1
            if await labels.exists_active(ref.column, ref.db, ref.table):
                await jobs.update_job(job_id, scanned_cols=scanned,
                                      current_db=ref.db, current_table=ref.table)
                continue
            try:
                vals = await asyncio.to_thread(connector.sample_values, ref, sample_n)
            except Exception as e:  # table illisible -> on continue
                _logger.warning("scan.sample_failed", extra={"table": ref.table, "err": str(e)})
                await jobs.update_job(job_id, scanned_cols=scanned)
                continue
            if not vals:
                await jobs.update_job(job_id, scanned_cols=scanned)
                continue
            meta = [value_metadata(v) for v in vals]
            label, conf = await classifier.classify(
                table=ref.table, column=ref.column, sql_type=ref.sql_type, value_metadata=meta)
            if label:
                status = "active" if conf >= threshold else "pending"
                await labels.upsert_ctx(header=ref.column, label=label, source="qwen3",
                                        confidence=conf, status=status, db_name=ref.db,
                                        table_name=ref.table, sample_values=vals[:3])
                found += 1
            await jobs.update_job(job_id, scanned_cols=scanned, found_labels=found,
                                  current_db=ref.db, current_table=ref.table)
        await jobs.finish_job(job_id, status="done")
    except Exception as e:  # noqa: BLE001
        _logger.error("scan.failed", extra={"job": job_id, "err": str(e)})
        await jobs.finish_job(job_id, status="failed", error=str(e))
```

Note : la classify renvoie `(label, confidence)` (signature existante de `ColumnClassifier.classify`). On ne logue jamais `vals` (seulement table/colonne).

- [ ] **Step 4: Lancer → PASS**
Run: `cd gateway && python -m pytest tests/test_scanner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**
```bash
git add gateway/scanner.py gateway/tests/test_scanner.py
git commit -m "feat(gateway): moteur de scan (run_scan) avec skip incrémental + fail-safe par table"
```

---

## Task 8 : Endpoints admin (dwh_router)

**Files:**
- Create: `gateway/dwh_router.py`
- Modify: `gateway/main.py` (wiring + include_router)
- Test: `gateway/tests/test_dwh_router.py`

- [ ] **Step 1: Écrire le test e2e (échoue) — source SQLite + classifier mocké**

```python
# gateway/tests/test_dwh_router.py
import sqlite3, tempfile, os, asyncio, pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient, ASGITransport

ADMIN = {"X-Admin-Secret": os.environ.get("ADMIN_SECRET", "test-admin")}

def _sqlite():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE clients (nom TEXT, email TEXT)")
    con.execute("INSERT INTO clients VALUES ('David','d@x.ch')")
    con.commit(); con.close(); return p

@pytest.mark.asyncio
async def test_create_scan_flow(app, monkeypatch):
    monkeypatch.setenv("DWH_ENC_KEY", Fernet.generate_key().decode())
    # classifier mocké : pas besoin d'Ollama
    class FakeClf:
        async def classify(self, table, column, sql_type, values=None, value_metadata=None):
            return ("EMAIL" if column == "email" else "PERSONNE", 0.95)
    app.state.classifier = FakeClf()
    path = _sqlite()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/admin/dwh_sources", headers=ADMIN, json={
            "name": "s1", "db_type": "sqlite", "options": {"sqlite_path": path}, "db_filter": []})
        assert r.status_code == 200
        sid = r.json()["id"]
        r = await c.post(f"/admin/dwh_sources/{sid}/scan", headers=ADMIN, json={"dbs": ["main"]})
        job_id = r.json()["job_id"]
        # poll jusqu'à done
        for _ in range(50):
            await asyncio.sleep(0.1)
            st = (await c.get(f"/admin/dwh_sources/scan/{job_id}", headers=ADMIN)).json()
            if st["status"] in ("done", "failed"):
                break
        assert st["status"] == "done"
        assert st["found_labels"] >= 2
```

**Note engineer :** la fixture `app` doit fournir l'app FastAPI avec `app.state.db_pool` + `app.state.column_labels` initialisés (voir `gateway/tests/conftest.py`). Réutiliser la fixture existante des tests admin.

- [ ] **Step 2: Lancer → FAIL**
Run: `cd gateway && python -m pytest tests/test_dwh_router.py -v`
Expected: FAIL (404 / no route)

- [ ] **Step 3: Implémenter le router**
```python
# gateway/dwh_router.py
from __future__ import annotations
import asyncio
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from gateway.dwh_sources import DwhSourceStore, decrypt_secret
from gateway.db_connectors import get_connector
from gateway.scanner import run_scan

router = APIRouter(prefix="/admin", tags=["dwh"])


def _require_admin(request: Request):
    secret = request.headers.get("X-Admin-Secret")
    if not secret or secret != os.environ.get("ADMIN_SECRET"):
        raise HTTPException(status_code=401, detail="admin secret invalide")


class SourceIn(BaseModel):
    name: str
    db_type: str
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    options: dict = {}
    db_filter: list[str] = []


class ScanIn(BaseModel):
    dbs: list[str] = []


def _store(request: Request) -> DwhSourceStore:
    return DwhSourceStore(request.app.state.db_pool)


@router.get("/dwh_sources", dependencies=[Depends(_require_admin)])
async def list_sources(request: Request):
    return await _store(request).list()


@router.post("/dwh_sources", dependencies=[Depends(_require_admin)])
async def create_source(request: Request, body: SourceIn):
    return await _store(request).create(**body.model_dump())


@router.put("/dwh_sources/{sid}", dependencies=[Depends(_require_admin)])
async def update_source(request: Request, sid: int, body: SourceIn):
    return await _store(request).update(sid, **body.model_dump())


@router.delete("/dwh_sources/{sid}", dependencies=[Depends(_require_admin)])
async def delete_source(request: Request, sid: int):
    await _store(request).delete(sid)
    return {"ok": True}


@router.post("/dwh_sources/{sid}/test", dependencies=[Depends(_require_admin)])
async def test_source(request: Request, sid: int):
    src = await _store(request).get(sid)
    if not src:
        raise HTTPException(404, "source inconnue")
    src = _with_password(src)
    try:
        dbs = await asyncio.to_thread(get_connector(src).list_databases)
        return {"ok": True, "databases": dbs}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"connexion échouée: {e}")


@router.post("/dwh_sources/{sid}/scan", dependencies=[Depends(_require_admin)])
async def start_scan(request: Request, sid: int, body: ScanIn):
    store = _store(request)
    src = await store.get(sid)
    if not src:
        raise HTTPException(404, "source inconnue")
    src = _with_password(src)
    job = await store.create_job(sid)
    connector = get_connector(src)
    classifier = request.app.state.classifier
    labels = request.app.state.column_labels
    cfg = await request.app.state.config_store.get_ner_config()
    threshold = getattr(cfg, "qwen_auto_approve_threshold", 0.7)
    include_views = bool((src.get("options") or {}).get("include_views", True))
    dbs = body.dbs or list(src.get("db_filter") or []) or await asyncio.to_thread(connector.list_databases)

    async def _bg():
        await run_scan(connector=connector, classifier=classifier, labels=labels, jobs=store,
                       job_id=job["id"], dbs=dbs, include_views=include_views, threshold=threshold)
        st = await store.get_job(job["id"])
        await store.set_last_scan(sid, st["status"])

    asyncio.create_task(_bg())
    return {"job_id": job["id"]}


@router.get("/dwh_sources/scan/{job_id}", dependencies=[Depends(_require_admin)])
async def scan_status(request: Request, job_id: int):
    job = await _store(request).get_job(job_id)
    if not job:
        raise HTTPException(404, "job inconnu")
    return job


@router.post("/dwh_sources/scan/{job_id}/cancel", dependencies=[Depends(_require_admin)])
async def scan_cancel(request: Request, job_id: int):
    await _store(request).update_job(job_id, status="cancelled")
    return {"ok": True}


def _with_password(src: dict) -> dict:
    """Déchiffre le password et l'injecte sous _password (jamais persisté/loggé)."""
    import json
    out = dict(src)
    if isinstance(out.get("options"), str):
        out["options"] = json.loads(out["options"])
    enc = out.get("password_encrypted")
    out["_password"] = decrypt_secret(enc) if enc else None
    return out
```

- [ ] **Step 4: Wirer dans `gateway/main.py`**

Après les autres `include_router` (vers la ligne 73-83), ajouter :
```python
from gateway.dwh_router import router as dwh_router
app.include_router(dwh_router)
```
(`DwhSourceStore` est instancié à la volée par le router via `app.state.db_pool` — pas besoin de l'ajouter à `app.state`.)

- [ ] **Step 5: Lancer → PASS**
Run: `cd gateway && python -m pytest tests/test_dwh_router.py -v`
Expected: PASS

- [ ] **Step 6: Commit**
```bash
git add gateway/dwh_router.py gateway/main.py gateway/tests/test_dwh_router.py
git commit -m "feat(gateway): endpoints /admin/dwh_sources (CRUD + test + scan + statut)"
```

---

## Task 9 : Dépendances + Dockerfile

**Files:**
- Modify: `gateway/requirements.txt`
- Modify: `gateway/Dockerfile`

- [ ] **Step 1: Ajouter les deps**

Ajouter à `gateway/requirements.txt` :
```
sqlalchemy==2.0.36
psycopg2-binary==2.9.10
pymysql==1.1.1
pyodbc==5.2.0
cryptography==44.0.0
```

- [ ] **Step 2: ODBC dans le Dockerfile gateway**

Dans `gateway/Dockerfile`, avant le `pip install`, ajouter l'installation ODBC + driver SQL Server (base Debian/slim) :
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg unixodbc unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Vérifier que l'image build**
Run: `docker build -t anon-gateway-test gateway/`
Expected: build OK (les imports sqlalchemy/pyodbc/cryptography résolvent).

- [ ] **Step 4: Commit**
```bash
git add gateway/requirements.txt gateway/Dockerfile
git commit -m "build(gateway): deps scan multi-DB (sqlalchemy, drivers, cryptography, ODBC)"
```

---

## Task 10 : Frontend — section « Sources de données »

**Files:**
- Modify: `frontend/admin.html`

**⚠️ Cette tâche utilise le skill `frontend-design`** pour une UI clean/sobre/belle cohérente avec l'existant. Valider une maquette avant d'écrire le markup final.

- [ ] **Step 1: Invoquer le skill frontend-design** pour concevoir la section (liste de sources en cartes, formulaire add/edit, bouton « Tester » → DB cochables, bouton « Lancer le scan » → barre de progression). Style aligné sur les sections existantes d'`admin.html`.

- [ ] **Step 2: Ajouter la section HTML** (nouveau `<section id="secDwh">`) avec : tableau/cartes des sources, formulaire, zone DB, barre de progression.

- [ ] **Step 3: Ajouter le JS** via `adminFetch()` (déjà défini) :
  - `loadSources()` → `GET /admin/dwh_sources`
  - `saveSource()` → `POST/PUT /admin/dwh_sources`
  - `testSource(id)` → `POST /admin/dwh_sources/{id}/test` → rendre les DB en cases à cocher
  - `startScan(id, dbs)` → `POST .../{id}/scan` → récupère `job_id`
  - `pollJob(job_id)` → `GET .../scan/{job_id}` toutes les 1s → maj barre `scanned/total`, `current_table`, `found_labels` ; stop quand `done|failed|cancelled`
  - à la fin du scan : appeler la fonction existante de rechargement de la section « À valider ».

- [ ] **Step 4: Test manuel** (documenté, pas automatisé)
```
docker compose up -d
# ouvrir http://localhost:3000/admin.html, saisir ADMIN_SECRET
# créer une source SQLite pointant sur /data/demo.sqlite (monté), tester, scanner, voir la barre + les pending
```

- [ ] **Step 5: Commit**
```bash
git add frontend/admin.html
git commit -m "feat(frontend): section Sources de données (config + scan + progression)"
```

---

## Task 11 : Documentation (SECURITY.md + ROADMAP.md)

**Files:**
- Modify: `docs/SECURITY.md`
- Modify: `ROADMAP.md`

- [ ] **Step 1: SECURITY.md — documenter le scan gateway-side**

Ajouter une sous-section « Scan multi-DB (gateway-side) » : le gateway lit des valeurs réelles pendant le scan (assouplissement zero-trust assumé) ; mitigations = compte lecture seule, réduction immédiate en métadonnées, aucun log des valeurs, 3 samples conservés pour revue, credentials chiffrés (Fernet, `DWH_ENC_KEY`). PII transitoire en RAM uniquement.

- [ ] **Step 2: ROADMAP.md — déplacer l'item dans « Fait »**

Couper « Scan multi-DB / multi-serveurs » de « Évolutions futures » → le résumer dans « Fait » avec les fichiers livrés (`gateway/dwh_*.py`, `gateway/scanner.py`, `gateway/db_connectors.py`, `frontend/admin.html`, tables `dwh_sources`/`scan_jobs`).

- [ ] **Step 3: Mettre à jour `.env.example`**

Ajouter dans `.env.example` :
```
# Scan multi-DB : clé Fernet pour chiffrer les credentials DWH (générer: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
DWH_ENC_KEY=
```

- [ ] **Step 4: Commit**
```bash
git add docs/SECURITY.md ROADMAP.md .env.example
git commit -m "docs: scan multi-DB (SECURITY zero-trust, ROADMAP fait, DWH_ENC_KEY)"
```

---

## Self-review (couverture spec)

- ✅ dwh_sources + scan_jobs (Task 1) ; chiffrement Fernet (Task 3) ; CRUD + jobs (Task 4)
- ✅ Connecteurs 4 moteurs + tables/vues + schémas système exclus (Task 5)
- ✅ value_metadata gateway-side (Task 2) ; upsert_ctx + skip incrémental (Task 6)
- ✅ Scan background + fail-safe par table + cancel + progression (Task 7, 8)
- ✅ Endpoints admin + test connexion + statut job (Task 8)
- ✅ Deps + ODBC Dockerfile (Task 9)
- ✅ UI section + progression, résultats → « À valider » existant (Task 10, frontend-design)
- ✅ Sécurité documentée + DWH_ENC_KEY + ROADMAP (Task 11)
- ✅ Tests : unit (value_metadata, crypto, connectors SQLite, upsert_ctx) + e2e scan (SQLite + classifier mocké)

Hors scope (assumé) : parallélisation, reprise de job après restart, dédoublonnage table/vue.

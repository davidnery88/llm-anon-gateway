# Admin Configuration Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PostgreSQL-backed admin interface (API + web UI) allowing an admin to configure active NER entity types, GLiNER confidence threshold, custom Presidio patterns, and manage API keys — all without code changes.

**Architecture:** Config stored in two new PostgreSQL tables (`ner_config` single-row + `custom_patterns`). A `NERConfig` dataclass wraps the built Presidio engine and active settings, held in `app.state.ner_config` and atomically replaced on admin writes. Admin endpoints on `/admin/*` are protected by `ADMIN_SECRET` env var via `X-Admin-Secret` header. A separate `frontend/admin.html` page provides the UI.

**Tech Stack:** FastAPI, asyncpg, Presidio (PatternRecognizer), vanilla JS (matching existing frontend style)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `scripts/init_db.sql` | Modify | Add `ner_config`, `custom_patterns` tables; `label` column on `api_keys` |
| `gateway/config_store.py` | Create | asyncpg CRUD for all config tables |
| `gateway/ner.py` | Modify | Add `NERConfig` dataclass; make `detect()` accept optional config |
| `gateway/admin_router.py` | Create | FastAPI router for `/admin/*` endpoints, `ADMIN_SECRET` auth |
| `gateway/main.py` | Modify | Load config at startup; mount admin router; pass config to anonymizer |
| `gateway/anonymizer.py` | Modify | Thread `NERConfig` through to `ner.detect()` |
| `frontend/admin.html` | Create | Admin UI: entities, threshold, patterns, API keys |
| `gateway/tests/test_ner_config.py` | Create | Tests for NERConfig and configurable detect() |
| `gateway/tests/test_admin.py` | Create | Tests for admin endpoints |

---

## Task 1: Database schema

**Files:**
- Modify: `scripts/init_db.sql`

- [ ] **Step 1: Write the failing test**

```python
# gateway/tests/test_admin.py
import pytest

def test_init_db_has_ner_config_table():
    """Placeholder — validated by running the migration in Step 5."""
    pass
```

Run: `pytest gateway/tests/test_admin.py::test_init_db_has_ner_config_table -v`
Expected: PASS (trivial placeholder, real validation is Step 5)

- [ ] **Step 2: Add tables to init_db.sql**

Replace the full content of `scripts/init_db.sql` with:

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    id          SERIAL PRIMARY KEY,
    user_id     UUID NOT NULL DEFAULT gen_random_uuid(),
    key_hash    CHAR(64) NOT NULL UNIQUE,
    label       VARCHAR(64),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_active   ON api_keys(key_hash) WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS ner_config (
    id               SERIAL PRIMARY KEY,
    active_labels    TEXT[] NOT NULL DEFAULT ARRAY[
        'PERSONNE','DATE','LOCALISATION','ORG',
        'AVS','IBAN','TEL','EMAIL','POLICE','CONTRAT'
    ],
    gliner_threshold FLOAT NOT NULL DEFAULT 0.5,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure exactly one config row always exists
INSERT INTO ner_config (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS custom_patterns (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(64) NOT NULL UNIQUE,
    regex        TEXT NOT NULL,
    entity_label VARCHAR(32) NOT NULL,
    score        FLOAT NOT NULL DEFAULT 0.8,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- [ ] **Step 3: Apply schema to running DB (if DB already exists)**

If the Docker DB is already initialized, run the ALTER manually:

```bash
docker compose exec postgres psql -U postgres anondb -c "
  ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS label VARCHAR(64);

  CREATE TABLE IF NOT EXISTS ner_config (
    id               SERIAL PRIMARY KEY,
    active_labels    TEXT[] NOT NULL DEFAULT ARRAY[
        'PERSONNE','DATE','LOCALISATION','ORG',
        'AVS','IBAN','TEL','EMAIL','POLICE','CONTRAT'
    ],
    gliner_threshold FLOAT NOT NULL DEFAULT 0.5,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  INSERT INTO ner_config (id) VALUES (1) ON CONFLICT DO NOTHING;

  CREATE TABLE IF NOT EXISTS custom_patterns (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(64) NOT NULL UNIQUE,
    regex        TEXT NOT NULL,
    entity_label VARCHAR(32) NOT NULL,
    score        FLOAT NOT NULL DEFAULT 0.8,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
"
```

Expected: `ALTER TABLE`, `CREATE TABLE`, `INSERT 0 1` (or `INSERT 0 0` if already exists), `CREATE TABLE`

- [ ] **Step 4: Commit**

```bash
git add scripts/init_db.sql gateway/tests/test_admin.py
git commit -m "feat: add ner_config and custom_patterns tables"
```

---

## Task 2: ConfigStore

**Files:**
- Create: `gateway/config_store.py`
- Modify: `gateway/tests/test_admin.py`

- [ ] **Step 1: Write the failing tests**

```python
# gateway/tests/test_admin.py  (replace the placeholder)
import pytest
from unittest.mock import AsyncMock
from gateway.config_store import ConfigStore


@pytest.fixture
def store(mock_db_pool):
    return ConfigStore(mock_db_pool)


@pytest.mark.asyncio
async def test_get_ner_config_returns_defaults(store, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={
        "active_labels": ["PERSONNE", "DATE"],
        "gliner_threshold": 0.5,
    })
    cfg = await store.get_ner_config()
    assert cfg["active_labels"] == ["PERSONNE", "DATE"]
    assert cfg["gliner_threshold"] == 0.5


@pytest.mark.asyncio
async def test_update_ner_config(store, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    await store.update_ner_config(["PERSONNE"], 0.7)
    mock_db_pool.execute.assert_called_once()
    call_args = mock_db_pool.execute.call_args[0]
    assert "UPDATE ner_config" in call_args[0]


@pytest.mark.asyncio
async def test_list_patterns(store, mock_db_pool):
    mock_db_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "name": "REF", "regex": r"REF-\d+", "entity_label": "REF_SINISTRE",
         "score": 0.9, "active": True, "created_at": "2026-01-01"}
    ])
    patterns = await store.list_patterns()
    assert len(patterns) == 1
    assert patterns[0]["name"] == "REF"


@pytest.mark.asyncio
async def test_create_pattern(store, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={"id": 1})
    result = await store.create_pattern("REF", r"REF-\d+", "REF_SINISTRE", 0.9)
    assert result["id"] == 1


@pytest.mark.asyncio
async def test_delete_pattern(store, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    await store.delete_pattern(1)
    mock_db_pool.execute.assert_called_once()


@pytest.mark.asyncio
async def test_list_api_keys(store, mock_db_pool):
    mock_db_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "user_id": "uuid-1", "label": "Olivier", "active": True, "created_at": "2026-01-01"}
    ])
    keys = await store.list_api_keys()
    assert keys[0]["label"] == "Olivier"


@pytest.mark.asyncio
async def test_create_api_key(store, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={"id": 1, "user_id": "uuid-1"})
    result = await store.create_api_key("Olivier")
    assert result["plain_key"].startswith("anon_")
    assert result["label"] == "Olivier"


@pytest.mark.asyncio
async def test_revoke_api_key(store, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    await store.revoke_api_key(1)
    mock_db_pool.execute.assert_called_once()
    assert "active = false" in mock_db_pool.execute.call_args[0][0].lower()
```

Run: `pytest gateway/tests/test_admin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gateway.config_store'`

- [ ] **Step 2: Implement ConfigStore**

Create `gateway/config_store.py`:

```python
from __future__ import annotations
import hashlib
import os
import secrets


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ConfigStore:
    def __init__(self, pool):
        self._pool = pool

    async def get_ner_config(self) -> dict:
        row = await self._pool.fetchrow(
            "SELECT active_labels, gliner_threshold FROM ner_config WHERE id = 1"
        )
        return {"active_labels": list(row["active_labels"]), "gliner_threshold": row["gliner_threshold"]}

    async def update_ner_config(self, active_labels: list[str], gliner_threshold: float) -> None:
        await self._pool.execute(
            "UPDATE ner_config SET active_labels = $1, gliner_threshold = $2, updated_at = NOW() WHERE id = 1",
            active_labels, gliner_threshold,
        )

    async def list_patterns(self) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT id, name, regex, entity_label, score, active, created_at FROM custom_patterns ORDER BY id"
        )
        return [dict(r) for r in rows]

    async def create_pattern(self, name: str, regex: str, entity_label: str, score: float) -> dict:
        row = await self._pool.fetchrow(
            "INSERT INTO custom_patterns (name, regex, entity_label, score) VALUES ($1, $2, $3, $4) RETURNING id",
            name, regex, entity_label, score,
        )
        return {"id": row["id"], "name": name, "regex": regex, "entity_label": entity_label, "score": score}

    async def delete_pattern(self, pattern_id: int) -> None:
        await self._pool.execute("DELETE FROM custom_patterns WHERE id = $1", pattern_id)

    async def list_api_keys(self) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT id, user_id, label, active, created_at FROM api_keys ORDER BY id"
        )
        return [dict(r) for r in rows]

    async def create_api_key(self, label: str) -> dict:
        plain_key = f"anon_{secrets.token_hex(32)}"
        key_hash = _hash_key(plain_key)
        row = await self._pool.fetchrow(
            "INSERT INTO api_keys (key_hash, label) VALUES ($1, $2) RETURNING id, user_id",
            key_hash, label,
        )
        return {"id": row["id"], "user_id": str(row["user_id"]), "label": label, "plain_key": plain_key}

    async def revoke_api_key(self, key_id: int) -> None:
        await self._pool.execute("UPDATE api_keys SET active = false WHERE id = $1", key_id)
```

- [ ] **Step 3: Run tests**

Run: `pytest gateway/tests/test_admin.py -v`
Expected: All 8 tests PASS

- [ ] **Step 4: Commit**

```bash
git add gateway/config_store.py gateway/tests/test_admin.py
git commit -m "feat: add ConfigStore for admin CRUD operations"
```

---

## Task 3: NERConfig + configurable detect()

**Files:**
- Modify: `gateway/ner.py`
- Create: `gateway/tests/test_ner_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# gateway/tests/test_ner_config.py
import pytest
from unittest.mock import MagicMock
from gateway.ner import NEREngine, NERConfig


@pytest.fixture
def ner_engine():
    engine = NEREngine.__new__(NEREngine)
    engine.gliner = MagicMock()
    engine.gliner.predict_entities = MagicMock(return_value=[
        {"text": "David Neri", "label": "person", "start": 0, "end": 10, "score": 0.8}
    ])
    engine._base_presidio = MagicMock()
    engine._base_presidio.analyze = MagicMock(return_value=[])
    return engine


def test_ner_config_default():
    cfg = NERConfig.default(engine=MagicMock())
    assert "PERSONNE" in cfg.active_labels
    assert cfg.gliner_threshold == 0.5


def test_detect_respects_active_labels(ner_engine):
    cfg = NERConfig(
        active_labels=frozenset({"DATE"}),  # PERSONNE excluded
        gliner_threshold=0.5,
        presidio=ner_engine._base_presidio,
    )
    entities = ner_engine.detect("David Neri a un sinistre", cfg)
    assert len(entities) == 0  # PERSONNE filtered out


def test_detect_respects_threshold(ner_engine):
    ner_engine.gliner.predict_entities = MagicMock(return_value=[
        {"text": "David Neri", "label": "person", "start": 0, "end": 10, "score": 0.3}
    ])
    cfg = NERConfig(
        active_labels=frozenset({"PERSONNE"}),
        gliner_threshold=0.5,  # score 0.3 < 0.5 → filtered
        presidio=ner_engine._base_presidio,
    )
    entities = ner_engine.detect("David Neri a un sinistre", cfg)
    assert len(entities) == 0


def test_detect_without_config_uses_defaults(ner_engine):
    ner_engine._base_presidio.analyze = MagicMock(return_value=[])
    entities = ner_engine.detect("David Neri a un sinistre")
    assert any(e.label == "PERSONNE" for e in entities)
```

Run: `pytest gateway/tests/test_ner_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'NERConfig'`

- [ ] **Step 2: Add NERConfig and refactor detect()**

Replace the full content of `gateway/ner.py`:

```python
from __future__ import annotations
import os
from dataclasses import dataclass
from gliner import GLiNER
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_analyzer.nlp_engine import NlpEngineProvider

GLINER_MODEL = os.environ.get("GLINER_MODEL", "urchade/gliner_multi_pii-v1")

GLINER_LABELS = [
    "person", "date", "location", "organization",
    "avs number", "iban", "phone number", "email address",
    "policy number", "contract number",
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
}

ALL_LABELS = frozenset(GLINER_TO_TOKEN.values())


@dataclass
class Entity:
    text: str
    label: str
    start: int
    end: int


@dataclass
class NERConfig:
    active_labels: frozenset
    gliner_threshold: float
    presidio: AnalyzerEngine

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
    ) -> "NERConfig":
        presidio = base_engine._build_presidio(extra_patterns)
        return NERConfig(
            active_labels=frozenset(active_labels),
            gliner_threshold=gliner_threshold,
            presidio=presidio,
        )


class NEREngine:
    def __init__(self, gliner_model: str = GLINER_MODEL):
        self.gliner = GLiNER.from_pretrained(gliner_model)
        self._base_presidio = self._build_presidio([])

    def _build_presidio(self, extra_patterns: list[dict]) -> AnalyzerEngine:
        avs = PatternRecognizer(
            supported_entity="AVS_NUMBER",
            patterns=[Pattern("AVS", r"756\.\d{4}\.\d{4}\.\d{2}", 0.9)],
        )
        policy = PatternRecognizer(
            supported_entity="POLICY_NUMBER",
            patterns=[Pattern("POLICY", r"\b[A-Z]{1,3}-\d{5,10}\b", 0.75)],
        )
        contract = PatternRecognizer(
            supported_entity="CONTRACT_NUMBER",
            patterns=[Pattern("CONTRACT", r"\bCT-\d{5,10}\b", 0.75)],
        )
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "fr", "model_name": "fr_core_news_md"}],
        })
        engine = AnalyzerEngine(
            nlp_engine=provider.create_engine(),
            supported_languages=["fr"],
        )
        engine.registry.add_recognizer(avs)
        engine.registry.add_recognizer(policy)
        engine.registry.add_recognizer(contract)
        for p in extra_patterns:
            recognizer = PatternRecognizer(
                supported_entity=p["entity_label"],
                patterns=[Pattern(p["name"], p["regex"], p["score"])],
            )
            engine.registry.add_recognizer(recognizer)
        return engine

    def detect(self, text: str, config: NERConfig | None = None) -> list[Entity]:
        if config is None:
            config = NERConfig.default(self)

        covered: set[tuple[int, int]] = set()
        entities: list[Entity] = []

        for e in self.gliner.predict_entities(text, GLINER_LABELS, threshold=config.gliner_threshold):
            span = (e["start"], e["end"])
            label = GLINER_TO_TOKEN.get(e["label"])
            if label and label in config.active_labels and span not in covered:
                entities.append(Entity(text=e["text"], label=label, start=e["start"], end=e["end"]))
                covered.add(span)

        for r in config.presidio.analyze(text=text, language="fr"):
            span = (r.start, r.end)
            label = PRESIDIO_TO_TOKEN.get(r.entity_type)
            if not label:
                # custom pattern — entity_type IS the label
                label = r.entity_type
            if label and label in config.active_labels and span not in covered:
                entities.append(Entity(text=text[r.start:r.end], label=label, start=r.start, end=r.end))
                covered.add(span)

        return entities
```

- [ ] **Step 3: Run new tests**

Run: `pytest gateway/tests/test_ner_config.py -v`
Expected: All 4 tests PASS

- [ ] **Step 4: Run existing NER tests to check no regression**

Run: `pytest gateway/tests/test_ner.py -v`
Expected: All 3 tests PASS

> Note: existing tests call `ner_engine.detect(text)` without config — this still works because config defaults to `NERConfig.default(self)`.
> However `ner_engine._base_presidio` must exist on the fixture. Update `test_ner.py` fixture:
>
> ```python
> @pytest.fixture
> def ner_engine():
>     engine = NEREngine.__new__(NEREngine)
>     engine.gliner = MagicMock()
>     engine._base_presidio = MagicMock()
>     engine._base_presidio.analyze = MagicMock(return_value=[])
>     return engine
> ```
>
> Replace `engine.presidio = MagicMock()` lines with `engine._base_presidio = MagicMock()` and `engine._base_presidio.analyze = MagicMock(return_value=[])` in `gateway/tests/test_ner.py`.

- [ ] **Step 5: Update test_ner.py fixture**

In `gateway/tests/test_ner.py`, replace the `ner_engine` fixture:

```python
@pytest.fixture
def ner_engine():
    engine = NEREngine.__new__(NEREngine)
    engine.gliner = MagicMock()
    engine._base_presidio = MagicMock()
    engine._base_presidio.analyze = MagicMock(return_value=[])
    return engine
```

And in each test that sets `ner_engine.presidio.analyze`, change to `ner_engine._base_presidio.analyze`.

- [ ] **Step 6: Run full test suite**

Run: `pytest gateway/tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add gateway/ner.py gateway/tests/test_ner.py gateway/tests/test_ner_config.py
git commit -m "feat: add NERConfig dataclass and configurable detect() threshold + label filtering"
```

---

## Task 4: Admin router

**Files:**
- Create: `gateway/admin_router.py`
- Modify: `gateway/tests/test_admin.py`

- [ ] **Step 1: Write the failing tests**

Add to `gateway/tests/test_admin.py`:

```python
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI


ADMIN_SECRET = "test-secret"


@pytest.fixture
def admin_app(mock_db_pool):
    os.environ["ADMIN_SECRET"] = ADMIN_SECRET
    from gateway.admin_router import router
    app = FastAPI()
    app.state.db_pool = mock_db_pool
    app.state.ner_config = MagicMock()
    app.state.anonymizer = MagicMock()
    app.include_router(router)
    return TestClient(app)


def test_admin_requires_secret(admin_app):
    resp = admin_app.get("/admin/config")
    assert resp.status_code == 403


def test_admin_get_config(admin_app, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={
        "active_labels": ["PERSONNE", "DATE"],
        "gliner_threshold": 0.5,
    })
    mock_db_pool.fetch = AsyncMock(return_value=[])
    resp = admin_app.get("/admin/config", headers={"X-Admin-Secret": ADMIN_SECRET})
    assert resp.status_code == 200
    data = resp.json()
    assert "active_labels" in data
    assert "gliner_threshold" in data


def test_admin_update_config(admin_app, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    mock_db_pool.fetchrow = AsyncMock(return_value={
        "active_labels": ["PERSONNE"],
        "gliner_threshold": 0.7,
    })
    mock_db_pool.fetch = AsyncMock(return_value=[])
    resp = admin_app.put(
        "/admin/config",
        json={"active_labels": ["PERSONNE"], "gliner_threshold": 0.7},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status_code == 200


def test_admin_create_pattern(admin_app, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={"id": 1})
    resp = admin_app.post(
        "/admin/patterns",
        json={"name": "REF", "regex": r"REF-\d+", "entity_label": "REF_SINISTRE", "score": 0.9},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == 1


def test_admin_delete_pattern(admin_app, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    resp = admin_app.delete("/admin/patterns/1", headers={"X-Admin-Secret": ADMIN_SECRET})
    assert resp.status_code == 200


def test_admin_list_keys(admin_app, mock_db_pool):
    mock_db_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "user_id": "uuid-1", "label": "Olivier", "active": True, "created_at": "2026-01-01"}
    ])
    resp = admin_app.get("/admin/keys", headers={"X-Admin-Secret": ADMIN_SECRET})
    assert resp.status_code == 200
    assert resp.json()[0]["label"] == "Olivier"


def test_admin_create_key(admin_app, mock_db_pool):
    mock_db_pool.fetchrow = AsyncMock(return_value={"id": 1, "user_id": "uuid-1"})
    resp = admin_app.post(
        "/admin/keys",
        json={"label": "Olivier"},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["plain_key"].startswith("anon_")


def test_admin_revoke_key(admin_app, mock_db_pool):
    mock_db_pool.execute = AsyncMock()
    resp = admin_app.delete("/admin/keys/1", headers={"X-Admin-Secret": ADMIN_SECRET})
    assert resp.status_code == 200
```

Run: `pytest gateway/tests/test_admin.py -k "admin_requires" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gateway.admin_router'`

- [ ] **Step 2: Implement admin_router.py**

Create `gateway/admin_router.py`:

```python
from __future__ import annotations
import os
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from gateway.config_store import ConfigStore
from gateway.ner import NERConfig


def _require_admin(request: Request) -> None:
    secret = os.environ.get("ADMIN_SECRET", "")
    if not secret or request.headers.get("X-Admin-Secret") != secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access denied")


router = APIRouter(prefix="/admin", dependencies=[Depends(_require_admin)])


def _store(request: Request) -> ConfigStore:
    return ConfigStore(request.app.state.db_pool)


async def _rebuild_ner_config(request: Request) -> None:
    store = _store(request)
    raw = await store.get_ner_config()
    patterns = await store.list_patterns()
    active = [p for p in patterns if p["active"]]
    request.app.state.ner_config = NERConfig.build(
        active_labels=raw["active_labels"],
        gliner_threshold=raw["gliner_threshold"],
        extra_patterns=active,
        base_engine=request.app.state.anonymizer.ner,
    )


class ConfigUpdate(BaseModel):
    active_labels: list[str]
    gliner_threshold: float


class PatternCreate(BaseModel):
    name: str
    regex: str
    entity_label: str
    score: float = 0.8


class KeyCreate(BaseModel):
    label: str


@router.get("/config")
async def get_config(request: Request):
    store = _store(request)
    cfg = await store.get_ner_config()
    patterns = await store.list_patterns()
    return {**cfg, "patterns": patterns}


@router.put("/config")
async def update_config(request: Request, body: ConfigUpdate):
    store = _store(request)
    await store.update_ner_config(body.active_labels, body.gliner_threshold)
    await _rebuild_ner_config(request)
    return {"status": "updated"}


@router.get("/patterns")
async def list_patterns(request: Request):
    return await _store(request).list_patterns()


@router.post("/patterns")
async def create_pattern(request: Request, body: PatternCreate):
    result = await _store(request).create_pattern(body.name, body.regex, body.entity_label, body.score)
    await _rebuild_ner_config(request)
    return result


@router.delete("/patterns/{pattern_id}")
async def delete_pattern(request: Request, pattern_id: int):
    await _store(request).delete_pattern(pattern_id)
    await _rebuild_ner_config(request)
    return {"status": "deleted"}


@router.get("/keys")
async def list_keys(request: Request):
    return await _store(request).list_api_keys()


@router.post("/keys")
async def create_key(request: Request, body: KeyCreate):
    return await _store(request).create_api_key(body.label)


@router.delete("/keys/{key_id}")
async def revoke_key(request: Request, key_id: int):
    await _store(request).revoke_api_key(key_id)
    return {"status": "revoked"}
```

- [ ] **Step 3: Run admin tests**

Run: `pytest gateway/tests/test_admin.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add gateway/admin_router.py gateway/tests/test_admin.py
git commit -m "feat: add admin router with config, patterns and API key endpoints"
```

---

## Task 5: Wire into main.py + anonymizer

**Files:**
- Modify: `gateway/main.py`
- Modify: `gateway/anonymizer.py`

- [ ] **Step 1: Update anonymizer to thread config through**

In `gateway/anonymizer.py`, update `anonymize`, `_anonymize_freetext`, and `_anonymize_structured` to accept and pass `NERConfig`:

```python
from __future__ import annotations

from gateway.cache import CacheService
from gateway.column_classifier import ColumnClassifier
from gateway.formats import (
    FieldValue, detect_format, extract_pairs, reinject, augment_pairs_as_text,
)
from gateway.ner import NEREngine, NERConfig, Entity

SAMPLE_SIZE = 5


class Anonymizer:
    def __init__(self, ollama_url: str = "http://localhost:11434", gliner_model: str | None = None):
        kwargs = {"gliner_model": gliner_model} if gliner_model else {}
        self.ner = NEREngine(**kwargs)
        self.classifier = ColumnClassifier(ollama_url=ollama_url)

    async def anonymize(
        self, text: str, cache: CacheService, user_id: str, config: NERConfig | None = None
    ) -> tuple[str, dict]:
        fmt = detect_format(text)
        if fmt == "freetext":
            return await self._anonymize_freetext(text, cache, user_id, config)
        return await self._anonymize_structured(text, fmt, cache, user_id, config)

    async def _anonymize_freetext(
        self, text: str, cache: CacheService, user_id: str, config: NERConfig | None = None
    ) -> tuple[str, dict]:
        entities = self.ner.detect(text, config)
        replacement: dict[str, str] = {}
        for entity in entities:
            token = await cache.get_or_create_token(user_id, entity.label, entity.text)
            replacement[entity.text] = token
        result = reinject(text, "freetext", replacement)
        mapping = {v: k for k, v in replacement.items()}
        return result, mapping

    async def _anonymize_structured(
        self, text: str, fmt: str, cache: CacheService, user_id: str, config: NERConfig | None = None
    ) -> tuple[str, dict]:
        pairs = extract_pairs(text, fmt)
        if not pairs:
            return await self._anonymize_freetext(text, cache, user_id, config)

        columns: dict[str, list[FieldValue]] = {}
        for p in pairs:
            columns.setdefault(p.field, []).append(p)

        replacement: dict[str, str] = {}

        for field, field_pairs in columns.items():
            values = [p.value for p in field_pairs]
            augmented = augment_pairs_as_text(field_pairs)

            entities = self.ner.detect(augmented, config)
            detected_values = {e.text for e in entities}

            for entity in entities:
                if entity.text in values and entity.text not in replacement:
                    token = await cache.get_or_create_token(user_id, entity.label, entity.text)
                    replacement[entity.text] = token

            unresolved = [v for v in values if v not in detected_values and v not in replacement]
            if unresolved:
                sample = unresolved[:SAMPLE_SIZE]
                label = await self.classifier.classify(
                    table="unknown", column=field, sql_type="varchar", values=sample
                )
                if label:
                    for v in unresolved:
                        if v not in replacement:
                            token = await cache.get_or_create_token(user_id, label, v)
                            replacement[v] = token

        result = reinject(text, fmt, replacement)
        mapping = {v: k for k, v in replacement.items()}
        return result, mapping

    def deanonymize(self, text: str, mapping: dict) -> str:
        result = text
        for token, original in sorted(mapping.items(), key=lambda x: len(x[0]), reverse=True):
            result = result.replace(token, original)
        return result

    async def aclose(self) -> None:
        await self.classifier.aclose()
```

- [ ] **Step 2: Update main.py**

Replace the full content of `gateway/main.py`:

```python
from __future__ import annotations
import os
from contextlib import asynccontextmanager

import asyncpg
import httpx
import redis.asyncio as redis
from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware

from gateway.admin_router import router as admin_router
from gateway.anonymizer import Anonymizer
from gateway.auth import validate_api_key
from gateway.cache import CacheService
from gateway.config_store import ConfigStore
from gateway.ner import NERConfig


def _key_from_auth(request: Request) -> str:
    return request.headers.get("Authorization", "").removeprefix("Bearer ")


limiter = Limiter(key_func=_key_from_auth)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await asyncpg.create_pool(os.environ["POSTGRES_DSN"])
    app.state.redis = redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379"),
        password=os.environ.get("REDIS_PASSWORD") or None,
        decode_responses=False,
    )
    app.state.anonymizer = Anonymizer(
        ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        gliner_model=os.environ.get("GLINER_MODEL"),
    )
    app.state.litellm_url = os.environ.get("LITELLM_URL", "http://litellm:4000")

    # Load NER config from DB
    store = ConfigStore(app.state.db_pool)
    raw = await store.get_ner_config()
    patterns = [p for p in await store.list_patterns() if p["active"]]
    app.state.ner_config = NERConfig.build(
        active_labels=raw["active_labels"],
        gliner_threshold=raw["gliner_threshold"],
        extra_patterns=patterns,
        base_engine=app.state.anonymizer.ner,
    )

    yield
    await app.state.db_pool.close()
    await app.state.redis.aclose()
    await app.state.anonymizer.aclose()


app = FastAPI(title="LLM Anonymization Gateway", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.include_router(admin_router)


class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str = "default"
    messages: list[ChatMessage]

class AnonymizeRequest(BaseModel):
    text: str

class DeanonymizeRequest(BaseModel):
    text: str


def get_cache(request: Request) -> CacheService:
    return CacheService(request.app.state.redis)


@app.post("/v1/chat/completions")
@limiter.limit("10/minute")
async def chat_completions(
    request: Request,
    body: ChatRequest,
    user_id: str = Depends(validate_api_key),
):
    cache = get_cache(request)
    anon = request.app.state.anonymizer
    cfg = request.app.state.ner_config

    anonymized_messages = []
    for msg in body.messages:
        if msg.role == "user":
            anon_text, _ = await anon.anonymize(msg.content, cache, user_id, cfg)
            anonymized_messages.append({"role": msg.role, "content": anon_text})
        else:
            anonymized_messages.append(msg.model_dump())

    async with httpx.AsyncClient() as http_client:
        resp = await http_client.post(
            f"{request.app.state.litellm_url}/v1/chat/completions",
            json={"model": body.model, "messages": anonymized_messages},
            timeout=60,
        )
        resp.raise_for_status()

    result = resp.json()
    mapping = await cache.get_mapping(user_id)
    for choice in result.get("choices", []):
        content = choice.get("message", {}).get("content")
        if content:
            choice["message"]["content"] = anon.deanonymize(content, mapping)

    return result


@app.post("/api/anonymize")
async def anonymize(
    request: Request,
    body: AnonymizeRequest,
    user_id: str = Depends(validate_api_key),
):
    cache = get_cache(request)
    cfg = request.app.state.ner_config
    anon_text, mapping = await request.app.state.anonymizer.anonymize(body.text, cache, user_id, cfg)
    return {"anonymized_text": anon_text, "mapping": mapping}


@app.get("/api/mapping")
async def get_mapping(
    request: Request,
    user_id: str = Depends(validate_api_key),
):
    return await get_cache(request).get_mapping(user_id)


@app.delete("/api/mapping")
async def clear_mapping(
    request: Request,
    user_id: str = Depends(validate_api_key),
):
    await get_cache(request).clear_mapping(user_id)
    return {"status": "cleared"}


@app.post("/api/deanonymize")
async def deanonymize(
    request: Request,
    body: DeanonymizeRequest,
    user_id: str = Depends(validate_api_key),
):
    mapping = await get_cache(request).get_mapping(user_id)
    result = request.app.state.anonymizer.deanonymize(body.text, mapping)
    return {"result": result}
```

- [ ] **Step 3: Run full test suite**

Run: `pytest gateway/tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add gateway/main.py gateway/anonymizer.py
git commit -m "feat: wire NERConfig through main.py and anonymizer, load from DB at startup"
```

---

## Task 6: Admin frontend

**Files:**
- Create: `frontend/admin.html`

- [ ] **Step 1: Create admin.html**

Create `frontend/admin.html`:

```html
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <title>Admin — Anonymisation Gateway</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #1a1a1a; }
    .container { max-width: 860px; margin: 2rem auto; padding: 0 1rem; }
    h1 { font-size: 1.25rem; font-weight: 600; margin-bottom: 2rem; }
    section { background: #fff; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem;
              border: 1px solid #e5e5e5; }
    h2 { font-size: 0.875rem; font-weight: 600; text-transform: uppercase;
         letter-spacing: 0.05em; color: #666; margin-bottom: 1rem; }
    label { display: block; font-size: 0.875rem; margin-bottom: 0.35rem; color: #444; }
    input[type=text], input[type=password], input[type=number] {
      width: 100%; padding: 0.5rem 0.75rem; border: 1px solid #d4d4d4;
      border-radius: 6px; font-size: 0.875rem; font-family: inherit; }
    input[type=range] { width: 100%; margin: 0.5rem 0; }
    button { padding: 0.5rem 1rem; border-radius: 6px; border: none; font-size: 0.875rem;
             cursor: pointer; font-family: inherit; }
    .btn-primary { background: #1a1a1a; color: #fff; }
    .btn-danger  { background: #dc2626; color: #fff; }
    .btn-sm      { padding: 0.25rem 0.6rem; font-size: 0.8rem; }
    .btn-primary:hover { background: #333; }
    .btn-danger:hover  { background: #b91c1c; }
    table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; margin-top: 0.5rem; }
    th { text-align: left; padding: 0.4rem 0.75rem; background: #f5f5f5;
         border-bottom: 1px solid #e5e5e5; font-weight: 600; }
    td { padding: 0.4rem 0.75rem; border-bottom: 1px solid #f0f0f0; font-family: monospace; }
    td.actions { font-family: inherit; }
    .row { display: flex; gap: 0.75rem; align-items: flex-end; flex-wrap: wrap; }
    .row > * { flex: 1; min-width: 120px; }
    .row > button { flex: 0 0 auto; }
    .labels-grid { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem; }
    .label-chip { display: flex; align-items: center; gap: 0.4rem; padding: 0.3rem 0.6rem;
                  border: 1px solid #d4d4d4; border-radius: 20px; font-size: 0.8125rem; cursor: pointer; }
    .label-chip input { width: auto; padding: 0; border: none; margin: 0; }
    .label-chip.active { background: #1a1a1a; color: #fff; border-color: #1a1a1a; }
    .empty { color: #999; font-size: 0.8125rem; padding: 0.5rem 0; }
    .error { color: #dc2626; font-size: 0.8125rem; margin-top: 0.5rem; }
    .success { color: #16a34a; font-size: 0.8125rem; margin-top: 0.5rem; }
    .secret-row { display: flex; gap: 0.75rem; align-items: flex-end; margin-bottom: 1.5rem; }
    .secret-row input { flex: 1; }
    .threshold-row { display: flex; gap: 1rem; align-items: center; }
    .threshold-row input[type=range] { flex: 1; }
    .threshold-val { font-weight: 600; min-width: 2.5rem; text-align: right; font-family: monospace; }
    .key-plain { background: #f0fdf4; border: 1px solid #86efac; border-radius: 6px;
                 padding: 0.75rem; font-family: monospace; font-size: 0.875rem;
                 word-break: break-all; margin-top: 0.75rem; }
  </style>
</head>
<body>
<div class="container">
  <h1>Admin — Anonymisation Gateway</h1>

  <section>
    <h2>Authentification admin</h2>
    <div class="secret-row">
      <input type="password" id="adminSecret" placeholder="ADMIN_SECRET">
      <button class="btn-primary" onclick="loadAll()">Charger la configuration</button>
    </div>
    <div id="authStatus"></div>
  </section>

  <section id="secEntities" style="display:none">
    <h2>Entités actives</h2>
    <p style="font-size:0.8rem;color:#666;margin-bottom:0.75rem;">
      Désactiver une entité = elle ne sera plus détectée ni tokenisée.
    </p>
    <div class="labels-grid" id="labelsGrid"></div>
    <button class="btn-primary" onclick="saveConfig()" style="margin-top:1rem;">Enregistrer</button>
    <div id="entitiesStatus"></div>
  </section>

  <section id="secThreshold" style="display:none">
    <h2>Seuil de confiance GLiNER</h2>
    <p style="font-size:0.8rem;color:#666;margin-bottom:0.75rem;">
      En dessous de ce seuil, une entité détectée par GLiNER est ignorée.
      Valeur recommandée : 0.5. Monter à 0.7+ pour moins de faux positifs.
    </p>
    <div class="threshold-row">
      <input type="range" id="thresholdRange" min="0.1" max="0.95" step="0.05"
             oninput="syncThreshold(this.value)">
      <span class="threshold-val" id="thresholdVal">0.50</span>
    </div>
    <button class="btn-primary" onclick="saveConfig()" style="margin-top:0.75rem;">Enregistrer</button>
    <div id="thresholdStatus"></div>
  </section>

  <section id="secPatterns" style="display:none">
    <h2>Patterns personnalisés (Presidio)</h2>
    <p style="font-size:0.8rem;color:#666;margin-bottom:0.75rem;">
      Ajouter des expressions régulières pour détecter des identifiants internes.
    </p>
    <div id="patternsTable"></div>
    <hr style="margin:1rem 0;border-color:#f0f0f0;">
    <p style="font-size:0.8rem;font-weight:600;margin-bottom:0.5rem;">Ajouter un pattern</p>
    <div class="row">
      <div><label>Nom</label><input type="text" id="pName" placeholder="REF_SINISTRE"></div>
      <div><label>Regex</label><input type="text" id="pRegex" placeholder="REF-\d{4}-\d{5}"></div>
      <div><label>Label token</label><input type="text" id="pLabel" placeholder="REF_SINISTRE"></div>
      <div><label>Score (0-1)</label><input type="number" id="pScore" value="0.8" min="0" max="1" step="0.05"></div>
      <button class="btn-primary" onclick="createPattern()">Ajouter</button>
    </div>
    <div id="patternsStatus"></div>
  </section>

  <section id="secKeys" style="display:none">
    <h2>Clés API</h2>
    <div id="keysTable"></div>
    <hr style="margin:1rem 0;border-color:#f0f0f0;">
    <p style="font-size:0.8rem;font-weight:600;margin-bottom:0.5rem;">Créer une clé</p>
    <div class="row">
      <div><label>Label (nom de l'utilisateur)</label><input type="text" id="keyLabel" placeholder="Olivier"></div>
      <button class="btn-primary" onclick="createKey()">Générer</button>
    </div>
    <div id="newKey"></div>
    <div id="keysStatus"></div>
  </section>
</div>

<script>
const ALL_LABELS = ["PERSONNE","DATE","LOCALISATION","ORG","AVS","IBAN","TEL","EMAIL","POLICE","CONTRAT"];
let activeLabels = new Set(ALL_LABELS);
let currentThreshold = 0.5;

const $ = id => document.getElementById(id);

function secret() { return $("adminSecret").value.trim(); }

async function adminFetch(path, opts = {}) {
  const s = secret();
  if (!s) { alert("Entrer le secret admin."); throw new Error("no secret"); }
  const res = await fetch(path, {
    ...opts,
    headers: { "X-Admin-Secret": s, "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function loadAll() {
  try {
    const data = await adminFetch("/admin/config");
    activeLabels = new Set(data.active_labels);
    currentThreshold = data.gliner_threshold;
    renderLabels();
    renderThreshold();
    await loadPatterns();
    await loadKeys();
    ["secEntities","secThreshold","secPatterns","secKeys"].forEach(id => $(id).style.display = "");
    $("authStatus").textContent = "";
  } catch(e) {
    $("authStatus").innerHTML = `<span class="error">Erreur : ${e.message}</span>`;
  }
}

function renderLabels() {
  $("labelsGrid").innerHTML = ALL_LABELS.map(label => `
    <div class="label-chip ${activeLabels.has(label) ? 'active' : ''}" onclick="toggleLabel('${label}', this)">
      ${label}
    </div>
  `).join("");
}

function toggleLabel(label, el) {
  if (activeLabels.has(label)) { activeLabels.delete(label); el.classList.remove("active"); }
  else { activeLabels.add(label); el.classList.add("active"); }
}

function renderThreshold() {
  $("thresholdRange").value = currentThreshold;
  $("thresholdVal").textContent = currentThreshold.toFixed(2);
}

function syncThreshold(val) {
  currentThreshold = parseFloat(val);
  $("thresholdVal").textContent = currentThreshold.toFixed(2);
}

async function saveConfig() {
  try {
    await adminFetch("/admin/config", {
      method: "PUT",
      body: JSON.stringify({ active_labels: [...activeLabels], gliner_threshold: currentThreshold }),
    });
    $("entitiesStatus").innerHTML = '<span class="success">Enregistré.</span>';
    $("thresholdStatus").innerHTML = '<span class="success">Enregistré.</span>';
  } catch(e) {
    $("entitiesStatus").innerHTML = `<span class="error">${e.message}</span>`;
  }
}

async function loadPatterns() {
  const patterns = await adminFetch("/admin/patterns");
  if (!patterns.length) {
    $("patternsTable").innerHTML = '<p class="empty">Aucun pattern personnalisé.</p>';
    return;
  }
  const rows = patterns.map(p => `
    <tr>
      <td>${p.name}</td>
      <td>${p.regex}</td>
      <td>${p.entity_label}</td>
      <td>${p.score}</td>
      <td class="actions">
        <button class="btn-danger btn-sm" onclick="deletePattern(${p.id})">Supprimer</button>
      </td>
    </tr>`).join("");
  $("patternsTable").innerHTML = `
    <table>
      <thead><tr><th>Nom</th><th>Regex</th><th>Label</th><th>Score</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function createPattern() {
  try {
    await adminFetch("/admin/patterns", {
      method: "POST",
      body: JSON.stringify({
        name: $("pName").value.trim(),
        regex: $("pRegex").value.trim(),
        entity_label: $("pLabel").value.trim(),
        score: parseFloat($("pScore").value),
      }),
    });
    ["pName","pRegex","pLabel"].forEach(id => $(id).value = "");
    $("pScore").value = "0.8";
    await loadPatterns();
    $("patternsStatus").innerHTML = '<span class="success">Pattern ajouté.</span>';
  } catch(e) {
    $("patternsStatus").innerHTML = `<span class="error">${e.message}</span>`;
  }
}

async function deletePattern(id) {
  if (!confirm("Supprimer ce pattern ?")) return;
  await adminFetch(`/admin/patterns/${id}`, { method: "DELETE" });
  await loadPatterns();
}

async function loadKeys() {
  const keys = await adminFetch("/admin/keys");
  if (!keys.length) {
    $("keysTable").innerHTML = '<p class="empty">Aucune clé API.</p>';
    return;
  }
  const rows = keys.map(k => `
    <tr>
      <td>${k.id}</td>
      <td>${k.label || "—"}</td>
      <td>${k.user_id}</td>
      <td>${k.active ? "✓ active" : "révoquée"}</td>
      <td>${k.created_at?.slice(0,10) || ""}</td>
      <td class="actions">
        ${k.active ? `<button class="btn-danger btn-sm" onclick="revokeKey(${k.id})">Révoquer</button>` : ""}
      </td>
    </tr>`).join("");
  $("keysTable").innerHTML = `
    <table>
      <thead><tr><th>#</th><th>Label</th><th>User ID</th><th>Statut</th><th>Créée le</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function createKey() {
  try {
    const label = $("keyLabel").value.trim();
    if (!label) { alert("Entrer un label."); return; }
    const data = await adminFetch("/admin/keys", {
      method: "POST",
      body: JSON.stringify({ label }),
    });
    $("newKey").innerHTML = `
      <p class="success" style="margin-top:0.5rem;">Clé générée — copier maintenant, elle ne sera plus affichée :</p>
      <div class="key-plain">${data.plain_key}</div>`;
    $("keyLabel").value = "";
    await loadKeys();
  } catch(e) {
    $("keysStatus").innerHTML = `<span class="error">${e.message}</span>`;
  }
}

async function revokeKey(id) {
  if (!confirm("Révoquer cette clé ? L'utilisateur perdra l'accès immédiatement.")) return;
  await adminFetch(`/admin/keys/${id}`, { method: "DELETE" });
  await loadKeys();
}
</script>
</body>
</html>
```

- [ ] **Step 2: Verify it's served by nginx**

The existing `docker-compose.yml` already mounts `./frontend:/usr/share/nginx/html:ro` — no config change needed. The page is accessible at `http://SERVER_IP:3000/admin.html`.

- [ ] **Step 3: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: add admin.html UI for entity config, patterns and API key management"
```

---

## Task 7: Add ADMIN_SECRET to env and README

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Add ADMIN_SECRET to .env.example**

Add the following line to `.env.example`:

```
# Générer avec : openssl rand -hex 32
ADMIN_SECRET=changeme
```

- [ ] **Step 2: Add admin section to README.md**

Add after the "## Déploiement équipe" section:

```markdown
## Interface d'administration

Accessible à `http://SERVER_IP:3000/admin.html` — requiert le `ADMIN_SECRET` défini dans `.env`.

| Section | Action |
|---|---|
| Entités actives | Activer / désactiver chaque type de PII détecté |
| Seuil GLiNER | Ajuster la sensibilité (0.1 = tout détecter, 0.9 = très strict) |
| Patterns personnalisés | Ajouter des regex pour les identifiants internes |
| Clés API | Créer et révoquer les clés des utilisateurs |

La configuration est persistée en base et rechargée à chaud — pas de redémarrage nécessaire.
```

- [ ] **Step 3: Run full test suite one last time**

Run: `pytest gateway/tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md
git commit -m "docs: add ADMIN_SECRET env var and admin UI section to README"
```

---

## Task 8: End-to-end tests

**Files:**
- Create: `gateway/tests/test_e2e.py`

These tests spin up the full FastAPI app in-process with a real Redis (via `fakeredis`) and mocked DB pool, exercising the complete anonymize → admin config change → re-anonymize cycle. No Docker, no real Postgres needed.

Install fakeredis if not already present:

```bash
pip install fakeredis
```

- [ ] **Step 1: Write the E2E tests**

Create `gateway/tests/test_e2e.py`:

```python
"""End-to-end tests — full FastAPI app, fakeredis, mocked asyncpg pool.

Covers:
- Anonymize free text via POST /api/anonymize
- Deanonymize via POST /api/deanonymize
- Session mapping via GET/DELETE /api/mapping
- Admin: update active labels → entity no longer anonymized
- Admin: add custom pattern → new entity tokenized
- Admin: update GLiNER threshold → low-confidence entity dropped
- Admin: create and revoke API key
"""
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


USER_KEY = "anon_testuser"
USER_HASH = __import__("hashlib").sha256(USER_KEY.encode()).hexdigest()
USER_UUID = "00000000-0000-0000-0000-000000000001"
ADMIN_SECRET = "e2e-admin-secret"


def _make_pool(extra_fetchrow=None):
    pool = AsyncMock()

    async def fetchrow(query, *args):
        if "key_hash" in query:
            return {"user_id": USER_UUID}
        if "ner_config" in query:
            return {"active_labels": ["PERSONNE","DATE","LOCALISATION","ORG","AVS","IBAN","TEL","EMAIL","POLICE","CONTRAT"], "gliner_threshold": 0.5}
        if extra_fetchrow:
            return await extra_fetchrow(query, *args)
        return None

    async def fetch(query, *args):
        return []  # no custom patterns, no api keys list by default

    async def execute(query, *args):
        pass

    pool.fetchrow = fetchrow
    pool.fetch = fetch
    pool.execute = execute
    return pool


@pytest.fixture
def app_client():
    import os
    os.environ["ADMIN_SECRET"] = ADMIN_SECRET
    os.environ["POSTGRES_DSN"] = "postgresql://fake"
    os.environ["REDIS_URL"] = "redis://fake"

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)

    with patch("gateway.main.asyncpg.create_pool", new_callable=AsyncMock) as mock_pool_create, \
         patch("gateway.main.redis.Redis.from_url", return_value=fake_redis), \
         patch("gateway.anonymizer.NEREngine") as MockNER, \
         patch("gateway.anonymizer.ColumnClassifier") as MockCC:

        ner = MagicMock()
        ner._base_presidio = MagicMock()
        ner._base_presidio.analyze = MagicMock(return_value=[])
        ner._build_presidio = MagicMock(return_value=ner._base_presidio)
        ner.gliner = MagicMock()
        ner.gliner.predict_entities = MagicMock(return_value=[
            {"text": "David Neri", "label": "person", "start": 0, "end": 10, "score": 0.8}
        ])

        cc = MagicMock()
        cc.classify = AsyncMock(return_value=None)
        cc.aclose = AsyncMock()

        MockNER.return_value = ner
        MockCC.return_value = cc

        pool = _make_pool()
        mock_pool_create.return_value = pool
        pool.close = AsyncMock()

        from gateway.main import app
        with TestClient(app) as client:
            client.app_ner = ner
            client.app_pool = pool
            yield client


def auth_headers():
    return {"Authorization": f"Bearer {USER_KEY}"}


def admin_headers():
    return {"X-Admin-Secret": ADMIN_SECRET}


# ── Core anonymize / deanonymize ─────────────────────────────────────────────

def test_e2e_anonymize_freetext(app_client):
    resp = app_client.post(
        "/api/anonymize",
        json={"text": "David Neri a un sinistre."},
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "David Neri" not in data["anonymized_text"]
    assert "PERSONNE_1" in data["anonymized_text"]
    assert data["mapping"]["PERSONNE_1"] == "David Neri"


def test_e2e_deanonymize(app_client):
    # Seed a mapping via anonymize first
    app_client.post(
        "/api/anonymize",
        json={"text": "David Neri a un sinistre."},
        headers=auth_headers(),
    )
    resp = app_client.post(
        "/api/deanonymize",
        json={"text": "PERSONNE_1 a un sinistre."},
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == "David Neri a un sinistre."


def test_e2e_get_mapping(app_client):
    app_client.post(
        "/api/anonymize",
        json={"text": "David Neri a un sinistre."},
        headers=auth_headers(),
    )
    resp = app_client.get("/api/mapping", headers=auth_headers())
    assert resp.status_code == 200
    assert "PERSONNE_1" in resp.json()


def test_e2e_clear_mapping(app_client):
    app_client.post(
        "/api/anonymize",
        json={"text": "David Neri a un sinistre."},
        headers=auth_headers(),
    )
    resp = app_client.delete("/api/mapping", headers=auth_headers())
    assert resp.status_code == 200
    mapping = app_client.get("/api/mapping", headers=auth_headers()).json()
    assert mapping == {}


# ── Admin: entity label toggle ────────────────────────────────────────────────

def test_e2e_disable_personne_stops_anonymization(app_client):
    # Disable PERSONNE label via admin
    app_client.app_pool.execute = AsyncMock()
    app_client.app_pool.fetchrow = AsyncMock(side_effect=lambda q, *a: (
        {"active_labels": ["DATE"], "gliner_threshold": 0.5}
        if "ner_config" in q else {"user_id": USER_UUID}
    ))
    app_client.app_pool.fetch = AsyncMock(return_value=[])

    resp = app_client.put(
        "/admin/config",
        json={"active_labels": ["DATE"], "gliner_threshold": 0.5},
        headers=admin_headers(),
    )
    assert resp.status_code == 200

    # Now anonymize — PERSONNE_1 should NOT appear (entity filtered)
    resp2 = app_client.post(
        "/api/anonymize",
        json={"text": "David Neri a un sinistre."},
        headers=auth_headers(),
    )
    assert resp2.status_code == 200
    assert "David Neri" in resp2.json()["anonymized_text"]  # not anonymized


# ── Admin: GLiNER threshold ───────────────────────────────────────────────────

def test_e2e_high_threshold_drops_low_confidence(app_client):
    # GLiNER returns score 0.3 for the entity
    app_client.app_ner.gliner.predict_entities = MagicMock(return_value=[
        {"text": "David Neri", "label": "person", "start": 0, "end": 10, "score": 0.3}
    ])

    app_client.app_pool.execute = AsyncMock()
    app_client.app_pool.fetchrow = AsyncMock(side_effect=lambda q, *a: (
        {"active_labels": ["PERSONNE","DATE"], "gliner_threshold": 0.7}
        if "ner_config" in q else {"user_id": USER_UUID}
    ))
    app_client.app_pool.fetch = AsyncMock(return_value=[])

    resp = app_client.put(
        "/admin/config",
        json={"active_labels": ["PERSONNE", "DATE"], "gliner_threshold": 0.7},
        headers=admin_headers(),
    )
    assert resp.status_code == 200

    resp2 = app_client.post(
        "/api/anonymize",
        json={"text": "David Neri a un sinistre."},
        headers=auth_headers(),
    )
    assert "David Neri" in resp2.json()["anonymized_text"]  # score 0.3 < 0.7 → not anonymized


# ── Admin: custom pattern ─────────────────────────────────────────────────────

def test_e2e_custom_pattern_tokenizes(app_client):
    # Patch _build_presidio to simulate a recognizer that detects REF-2025-04892
    from presidio_analyzer import RecognizerResult

    def build_with_pattern(extra_patterns):
        presidio = MagicMock()
        if any(p.get("name") == "REF" for p in extra_patterns):
            result = MagicMock(spec=RecognizerResult)
            result.entity_type = "REF_SINISTRE"
            result.start = 0
            result.end = 14
            presidio.analyze = MagicMock(return_value=[result])
        else:
            presidio.analyze = MagicMock(return_value=[])
        return presidio

    app_client.app_ner._build_presidio = build_with_pattern
    app_client.app_ner.gliner.predict_entities = MagicMock(return_value=[])

    app_client.app_pool.fetchrow = AsyncMock(side_effect=lambda q, *a: (
        {"id": 1} if "INSERT INTO custom_patterns" in q
        else {"active_labels": ["PERSONNE","REF_SINISTRE"], "gliner_threshold": 0.5}
        if "ner_config" in q else {"user_id": USER_UUID}
    ))
    app_client.app_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "name": "REF", "regex": r"REF-\d{4}-\d{5}", "entity_label": "REF_SINISTRE",
         "score": 0.9, "active": True}
    ])
    app_client.app_pool.execute = AsyncMock()

    resp = app_client.post(
        "/admin/patterns",
        json={"name": "REF", "regex": r"REF-\d{4}-\d{5}", "entity_label": "REF_SINISTRE", "score": 0.9},
        headers=admin_headers(),
    )
    assert resp.status_code == 200

    resp2 = app_client.post(
        "/api/anonymize",
        json={"text": "REF-2025-04892 a été traité."},
        headers=auth_headers(),
    )
    assert "REF-2025-04892" not in resp2.json()["anonymized_text"]


# ── Admin: API key lifecycle ──────────────────────────────────────────────────

def test_e2e_create_and_revoke_key(app_client):
    created = {"id": 42, "user_id": "uuid-new"}
    app_client.app_pool.fetchrow = AsyncMock(side_effect=lambda q, *a: (
        created if "INSERT INTO api_keys" in q else {"user_id": USER_UUID}
    ))
    app_client.app_pool.execute = AsyncMock()

    resp = app_client.post(
        "/admin/keys",
        json={"label": "TestUser"},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["plain_key"].startswith("anon_")
    assert data["label"] == "TestUser"

    resp2 = app_client.delete("/admin/keys/42", headers=admin_headers())
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "revoked"


def test_e2e_admin_blocked_without_secret(app_client):
    resp = app_client.get("/admin/config")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run E2E tests**

```bash
pip install fakeredis
pytest gateway/tests/test_e2e.py -v
```

Expected: All 9 tests PASS. If any fail, diagnose and fix the issue before proceeding.

- [ ] **Step 3: Run full test suite**

```bash
pytest gateway/tests/ -v
```

Expected: All tests PASS (unit + integration + E2E).

- [ ] **Step 4: Commit**

```bash
git add gateway/tests/test_e2e.py
git commit -m "test: add end-to-end tests covering full anonymize/admin/config cycle"
```

---

## Self-Review

**Spec coverage:**
- ✅ Entités actives/inactives — `NERConfig.active_labels`, toggles in UI, saved via `PUT /admin/config`
- ✅ Patterns regex custom — `custom_patterns` table, `POST/DELETE /admin/patterns`, rendered in UI
- ✅ Seuils de confiance GLiNER — `gliner_threshold` in `ner_config`, range slider in UI, passed to `predict_entities(threshold=...)`
- ✅ Gestion clés API — `GET/POST/DELETE /admin/keys`, rendered table, key shown once on creation
- ✅ Admin-only access — `X-Admin-Secret` header on all `/admin/*` endpoints

**Placeholder scan:** No TBD, no "add appropriate handling", no "similar to Task N". All code blocks complete.

**Type consistency:**
- `NERConfig.build(active_labels, gliner_threshold, extra_patterns, base_engine)` — used in Task 3 and Task 5 consistently
- `ConfigStore.get_ner_config()` returns `dict` with `active_labels` (list) and `gliner_threshold` (float) — consumed correctly in `main.py` and `admin_router.py`
- `detect(text, config: NERConfig | None = None)` — None defaults to `NERConfig.default(self)` — existing tests unaffected

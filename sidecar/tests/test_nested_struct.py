"""E2E : anonymisation récursive du JSON/XML imbriqué dans des valeurs de colonnes
(cas assurance : JSON/XML stocké dans varchar/nvarchar). NER + classifier mockés."""
import sys
import types
import pytest

# --- Stub des deps lourdes AVANT d'importer sidecar.anonymizer ---
def _ensure_stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

_ensure_stub("gliner", GLiNER=object)
_ensure_stub("presidio_analyzer", AnalyzerEngine=object, PatternRecognizer=object, Pattern=object)
_ensure_stub("presidio_analyzer.nlp_engine", NlpEngineProvider=object)

_NAMES = ["David Dupont", "Anna Muller"]


class FakeCache:
    def __init__(self): self.n = 0
    async def get_or_create_token(self, user_id, label, value):
        self.n += 1
        return f"[{label}_{self.n}]"


class FakeNER:
    def detect(self, text, config=None):
        from sidecar.ner import Entity
        out = []
        for name in _NAMES:
            i = text.find(name)
            if i != -1:
                out.append(Entity(text=name, label="PERSONNE", start=i, end=i + len(name),
                                  source="gliner", confidence=0.99))
        return out


class FakeClassifier:
    async def classify(self, table, column, sql_type="varchar", values=None, value_metadata=None):
        return (None, 0.0)
    async def aclose(self): pass


class FakeLabels:
    async def lookup(self, *a, **k): return (None, "none")
    async def increment_occurrence(self, *a, **k): pass
    async def upsert(self, *a, **k): return {}


@pytest.fixture
def anonymizer(monkeypatch):
    import sidecar.anonymizer as am
    monkeypatch.setattr(am, "NEREngine", lambda **kw: FakeNER())
    monkeypatch.setattr(am, "ColumnClassifier", lambda **kw: FakeClassifier())
    return am.Anonymizer(column_labels=FakeLabels(), events_bus=None, audit_client=None)


async def _run(anonymizer, text):
    return await anonymizer.anonymize(text, FakeCache(), user_id="u1")


@pytest.mark.asyncio
async def test_sql_literal_with_json_blob(anonymizer):
    # cas assurance : JSON dans un littéral SQL (colonne varchar)
    text = """INSERT INTO t (id, details) VALUES (1, '{"nom": "David Dupont", "montant": 1500}')"""
    result, mapping = await _run(anonymizer, text)
    assert "David Dupont" not in result          # PII interne masquée
    assert "1500" in result                       # non-PII gardé
    assert "details" in result and "INSERT" in result  # structure SQL intacte
    assert "David Dupont" in mapping.values()     # réversible
    assert anonymizer.deanonymize(result, mapping).count("David Dupont") == 1


@pytest.mark.asyncio
async def test_json_value_containing_json_string(anonymizer):
    text = '{"meta": "x", "details": "{\\"nom\\": \\"David Dupont\\"}"}'
    result, mapping = await _run(anonymizer, text)
    assert "David Dupont" not in result
    assert "David Dupont" in mapping.values()


@pytest.mark.asyncio
async def test_xml_blob_leaf(anonymizer):
    text = "<client><nom>David Dupont</nom><montant>1500</montant></client>"
    result, mapping = await _run(anonymizer, text)
    assert "David Dupont" not in result
    assert "<nom>" in result and "<client>" in result
    assert "David Dupont" in mapping.values()


@pytest.mark.asyncio
async def test_json_nested_in_xml(anonymizer):
    # récursion : XML dont une feuille contient du JSON
    text = '<rec><payload>{"nom": "Anna Muller"}</payload></rec>'
    result, mapping = await _run(anonymizer, text)
    assert "Anna Muller" not in result
    assert "Anna Muller" in mapping.values()


@pytest.mark.asyncio
async def test_blob_without_pii_unchanged(anonymizer):
    text = """INSERT INTO t (details) VALUES ('{"montant": 1500, "statut": "actif"}')"""
    result, mapping = await _run(anonymizer, text)
    assert "1500" in result and "actif" in result   # rien à masquer
    # pas de crash, pas de récursion infinie ; aucun nom inventé
    assert all("PERSONNE" not in t for t in mapping)

import sys, types, pytest

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
# (ajoute ici d'autres stubs si l'import échoue encore : spacy, redis, etc.)

class FakeCache:
    def __init__(self): self.n = 0
    async def get_or_create_token(self, user_id, label, value):
        self.n += 1
        return f"[{label}_{self.n}]"

class FakeNER:
    def detect(self, text, config=None):
        from sidecar.ner import Entity
        out = []
        i = text.find("Bob")
        if i != -1:
            out.append(Entity(text="Bob", label="PERSONNE", start=i, end=i+3, source="gliner", confidence=0.99))
        return out

class FakeClassifier:
    async def classify(self, table, column, sql_type="varchar", values=None, value_metadata=None):
        return ("PERSONNE", 0.95) if column == "nom" else (None, 0.0)
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
    # Construire avec la VRAIE signature (lire sidecar/anonymizer.py:25). Adapter si besoin.
    a = am.Anonymizer(column_labels=FakeLabels(), events_bus=None, audit_client=None)
    return a

@pytest.mark.asyncio
async def test_embedded_sql_in_prose(anonymizer):
    cache = FakeCache()
    text = "Bob a demandé : INSERT INTO clients (nom) VALUES ('David Dupont'); voilà."
    result, mapping = await anonymizer.anonymize(text, cache, user_id="u1")
    assert "David Dupont" not in result          # valeur SQL masquée
    assert "clients" in result and "nom" in result  # structure intacte
    assert "Bob" not in result                    # PII prose masquée
    assert "David Dupont" in mapping.values()     # réversible


@pytest.mark.asyncio
async def test_embedded_sql_without_pairs_no_recursion(anonymizer):
    # SQL embarqué sans valeurs extractibles : ne doit pas boucler (récursion
    # via le fallback freetext de _anonymize_structured) et laisser le texte intact.
    cache = FakeCache()
    text = "Regarde la requête SELECT * FROM clients stp"
    result, mapping = await anonymizer.anonymize(text, cache, user_id="u1")
    assert result == text
    assert mapping == {}

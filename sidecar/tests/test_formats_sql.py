from sidecar.formats import detect_format, extract_pairs, reinject

def test_detect_sql():
    assert detect_format("INSERT INTO c (nom) VALUES ('David')") == "sql"
    assert detect_format("  update c set nom='x' where id=1 ") == "sql"
    assert detect_format("SELECT * FROM c WHERE nom='David'") == "sql"

def test_detect_not_sql():
    assert detect_format("Bonjour, ceci est une phrase.") == "freetext"
    assert detect_format('{"a":1}') == "json"

def test_detect_sql_requires_parse():
    assert detect_format("SELECT ??? !!! pas du sql") != "sql"

def test_extract_and_reinject_sql():
    sql = "INSERT INTO clients (nom, email) VALUES ('David Dupont','d@x.ch')"
    pairs = extract_pairs(sql, "sql")
    fields = {(p.field, p.value) for p in pairs}
    assert ("nom", "David Dupont") in fields
    out = reinject(sql, "sql", {"David Dupont": "[PERSONNE_1]"})
    assert "[PERSONNE_1]" in out and "David Dupont" not in out

from sidecar.sql_anon import extract_sql_pairs, reinject_sql

def _pairs(sql):
    return {(p.field, p.value) for p in extract_sql_pairs(sql)}

def test_insert_pairs():
    p = _pairs("INSERT INTO clients (nom, email) VALUES ('David Dupont','d@x.ch')")
    assert ("nom", "David Dupont") in p and ("email", "d@x.ch") in p

def test_insert_multi_row():
    p = _pairs("INSERT INTO c (nom) VALUES ('David'),('Anna')")
    assert ("nom", "David") in p and ("nom", "Anna") in p

def test_update_set_and_where():
    p = _pairs("UPDATE c SET adresse='Rue du Lac 12' WHERE no_avs='7561234567890'")
    assert ("adresse", "Rue du Lac 12") in p and ("no_avs", "7561234567890") in p

def test_delete_where():
    assert ("email", "d@x.ch") in _pairs("DELETE FROM c WHERE email='d@x.ch'")

def test_select_where():
    assert ("nom", "David Dupont") in _pairs("SELECT * FROM c WHERE nom='David Dupont'")

def test_reinject_replaces_only_values():
    sql = "INSERT INTO clients (nom, email) VALUES ('David Dupont','d@x.ch')"
    out = reinject_sql(sql, {"David Dupont": "[PERSONNE_1]", "d@x.ch": "[EMAIL_1]"})
    assert "[PERSONNE_1]" in out and "[EMAIL_1]" in out
    assert "clients" in out and "nom" in out and "email" in out
    assert "David Dupont" not in out

def test_reinject_malformed_falls_back():
    out = reinject_sql("NOT;; SQL David", {"David": "[PERSONNE_1]"})
    assert "[PERSONNE_1]" in out

import pytest
from sidecar.sql_anon import find_sql_statements, splice_embedded_sql

def test_find_standalone():
    spans = find_sql_statements("INSERT INTO c (nom) VALUES ('David');")
    assert len(spans) == 1

def test_find_embedded_in_prose():
    text = "Corrige ce script : INSERT INTO c (nom) VALUES ('David'); merci"
    spans = find_sql_statements(text)
    assert len(spans) == 1
    s, e, frag = spans[0]
    assert frag.strip().startswith("INSERT") and frag.strip().endswith(";")
    assert text[s:e] == frag

def test_find_ignores_non_sql():
    assert find_sql_statements("juste une phrase sans requête") == []

@pytest.mark.asyncio
async def test_splice_embedded_sql():
    text = "Avant. UPDATE c SET nom='David' WHERE id=5; Après."
    async def fake_anon(sql):
        return sql.replace("'David'", "'[PERSONNE_1]'"), {"[PERSONNE_1]": "David"}
    new_text, mapping = await splice_embedded_sql(text, fake_anon)
    assert "[PERSONNE_1]" in new_text
    assert new_text.startswith("Avant.") and new_text.endswith("Après.")
    assert mapping == {"[PERSONNE_1]": "David"}


# --- Régression revue finale : pas de fuite sur IN / LIKE / inégalités / formes non prévues ---

def test_where_in_clause():
    p = _pairs("DELETE FROM clients WHERE statut='inactif' AND nom IN ('David Dupont','Anna Muller')")
    assert ("nom", "David Dupont") in p and ("nom", "Anna Muller") in p
    assert ("statut", "inactif") in p

def test_where_like():
    assert ("nom", "%Dupont%") in _pairs("SELECT * FROM c WHERE nom LIKE '%Dupont%'")

def test_where_inequality():
    assert ("email", "d@x.ch") in _pairs("SELECT * FROM c WHERE email != 'd@x.ch'")

def test_safety_net_catches_unmapped_string():
    # côté gauche = fonction (pas une Column) → pas de contexte, mais le filet le capte
    assert ("valeur", "DAVID") in _pairs("SELECT * FROM c WHERE upper(nom) = 'DAVID'")

def test_reinject_in_clause():
    out = reinject_sql("DELETE FROM c WHERE nom IN ('David Dupont','Anna')",
                       {"David Dupont": "[PERSONNE_1]", "Anna": "[PERSONNE_2]"})
    assert "[PERSONNE_1]" in out and "[PERSONNE_2]" in out and "David Dupont" not in out

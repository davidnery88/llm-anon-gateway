"""Unit tests for gateway.column_labels.fuzzy_lookup."""
from gateway.column_labels import fuzzy_lookup


KNOWN = [
    "nom_complet", "prenom", "email", "telephone", "telefonnummer",
    "adresse", "date_naissance", "iban", "avs",
]


def test_exact_match():
    assert fuzzy_lookup("email", KNOWN) == "email"
    assert fuzzy_lookup("nom_complet", KNOWN) == "nom_complet"


def test_close_variation_telefonnummer():
    # telefon_nummer should fuzzy-match telefonnummer
    assert fuzzy_lookup("telefon_nummer", KNOWN) == "telefonnummer"


def test_below_threshold_returns_none():
    # garbage input - far from any known header
    assert fuzzy_lookup("xyzqwert", KNOWN) is None


def test_ambiguity_returns_none():
    # 'contact' scores ~93 vs both 'contract' and 'contacts' (gap = 0)
    # → above threshold but ambiguous → None
    known_amb = ["contract", "contacts", "email"]
    assert fuzzy_lookup("contact", known_amb) is None


def test_unique_above_threshold_returns_match():
    # 'noms' vs only 'nom' → unique, above threshold
    known = ["nom", "email", "telephone"]
    # 'noms' vs 'nom' = ~85; below 88 → no match
    # use closer: 'emai' vs 'email' should be >88 unique
    assert fuzzy_lookup("emai", known) == "email"


def test_empty_header_returns_none():
    assert fuzzy_lookup("", KNOWN) is None


def test_empty_known_returns_none():
    assert fuzzy_lookup("email", []) is None


def test_threshold_boundary():
    # raise threshold so even close matches fail
    assert fuzzy_lookup("telefon_nummer", KNOWN, threshold=99) is None


def test_lower_threshold_allows_match():
    # 'phone' vs known including 'phone_number'-ish - token_set_ratio for
    # 'phone' vs 'telephone' won't match easily; verify with low threshold
    assert fuzzy_lookup("emial", KNOWN, threshold=70) == "email"

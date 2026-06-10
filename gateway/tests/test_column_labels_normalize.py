"""Unit tests for gateway.column_labels.normalize_header."""
from gateway.column_labels import normalize_header


def test_lowercase():
    assert normalize_header("NOM") == "nom"
    assert normalize_header("Nom") == "nom"


def test_strip_accents_numero_avs():
    assert normalize_header("Numéro_AVS") == "numero_avs"


def test_strip_accents_prenom_complet():
    assert normalize_header("Prénom Complet") == "prenom_complet"


def test_strip_accents_various():
    assert normalize_header("Adrésse") == "adresse"
    assert normalize_header("Téléphone") == "telephone"
    assert normalize_header("Ça va") == "ca_va"


def test_separator_space():
    assert normalize_header("Nom Complet") == "nom_complet"


def test_separator_dash():
    assert normalize_header("Nom-Complet") == "nom_complet"


def test_separator_dot():
    assert normalize_header("Nom.Complet") == "nom_complet"


def test_separator_slash():
    assert normalize_header("Nom/Complet") == "nom_complet"


def test_separator_tab():
    assert normalize_header("Nom\tComplet") == "nom_complet"


def test_collapse_multi_underscores():
    assert normalize_header("nom___complet") == "nom_complet"
    assert normalize_header("a__b____c") == "a_b_c"


def test_strip_leading_trailing_underscores():
    assert normalize_header("_nom_") == "nom"
    assert normalize_header("__nom__complet__") == "nom_complet"


def test_mixed_separators():
    assert normalize_header("Nom Complet-Du.Client") == "nom_complet_du_client"


def test_empty_string():
    assert normalize_header("") == ""


def test_whitespace_only():
    assert normalize_header("   ") == ""
    assert normalize_header("\t\t") == ""


def test_already_normalized():
    assert normalize_header("nom_complet") == "nom_complet"


def test_none_input():
    assert normalize_header(None) == ""

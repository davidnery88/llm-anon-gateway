from sidecar.formats import detect_format, extract_pairs, reinject


def test_detect_xml():
    assert detect_format("<client><nom>David</nom></client>") == "xml"
    assert detect_format("  <a><b>x</b></a> ") == "xml"


def test_detect_not_xml():
    assert detect_format("Bonjour < 3") != "xml"          # charabia non parsable
    assert detect_format('{"a":1}') == "json"
    assert detect_format("phrase normale") == "freetext"


def test_extract_xml_text_and_attribute():
    pairs = extract_pairs("<client nom='David'><email>d@x.ch</email></client>", "xml")
    fields = {(p.field, p.value) for p in pairs}
    assert ("nom", "David") in fields        # attribut
    assert ("email", "d@x.ch") in fields     # texte de feuille


def test_reinject_xml_replaces_leaves_only():
    xml = "<client><nom>David Dupont</nom><email>d@x.ch</email></client>"
    out = reinject(xml, "xml", {"David Dupont": "[PERSONNE_1]", "d@x.ch": "[EMAIL_1]"})
    assert "[PERSONNE_1]" in out and "[EMAIL_1]" in out
    assert "David Dupont" not in out
    assert "<nom>" in out and "<email>" in out and "<client>" in out  # structure intacte


def test_reinject_xml_attribute():
    out = reinject("<c nom='David'/>", "xml", {"David": "[PERSONNE_1]"})
    assert "[PERSONNE_1]" in out and "David" not in out


def test_mixed_content_tail_not_leaked():
    # contenu mixte : 'David Dupont' est le tail de <b>, après l'élément enfant
    xml = "<a>début <b>x</b> David Dupont fin</a>"
    pairs = extract_pairs(xml, "xml")
    # le tail est rattaché à l'élément qu'il suit (<b>) comme hint — l'important : il EST capté
    assert ("b", "David Dupont fin") in {(p.field, p.value) for p in pairs}
    out = reinject(xml, "xml", {"David Dupont fin": "[PERSONNE_1]"})
    assert "[PERSONNE_1]" in out and "David Dupont" not in out

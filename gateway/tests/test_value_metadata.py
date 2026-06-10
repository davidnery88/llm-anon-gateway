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

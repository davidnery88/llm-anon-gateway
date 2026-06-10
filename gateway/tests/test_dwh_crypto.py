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

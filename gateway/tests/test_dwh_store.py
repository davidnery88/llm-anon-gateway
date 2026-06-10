import pytest
from unittest.mock import AsyncMock
from cryptography.fernet import Fernet

ROW = {"id": 1, "name": "s", "db_type": "sqlite", "host": None, "port": None,
       "username": None, "options": {}, "db_filter": [], "last_scan_at": None,
       "last_scan_status": None, "created_at": None}

@pytest.mark.asyncio
async def test_create_encrypts_password(monkeypatch):
    monkeypatch.setenv("DWH_ENC_KEY", Fernet.generate_key().decode())
    from importlib import reload
    import gateway.dwh_sources as m; reload(m)
    pool = AsyncMock(); pool.fetchrow = AsyncMock(return_value=ROW)
    store = m.DwhSourceStore(pool)
    await store.create(name="s", db_type="sqlite", host=None, port=None,
                       username=None, password="secret", options={}, db_filter=[])
    args = pool.fetchrow.call_args.args            # positional SQL params
    assert "secret" not in args                     # plaintext never sent
    enc = args[6]                                    # args[0]=SQL, then $1..$6 -> password_encrypted
    assert m.decrypt_secret(enc) == "secret"        # but decryptable

@pytest.mark.asyncio
async def test_create_password_none_ok(monkeypatch):
    monkeypatch.setenv("DWH_ENC_KEY", Fernet.generate_key().decode())
    from importlib import reload
    import gateway.dwh_sources as m; reload(m)
    pool = AsyncMock(); pool.fetchrow = AsyncMock(return_value=ROW)
    store = m.DwhSourceStore(pool)
    await store.create(name="s", db_type="sqlite", host=None, port=None,
                       username=None, password=None, options={}, db_filter=[])
    assert pool.fetchrow.call_args.args[6] is None   # no password -> NULL

@pytest.mark.asyncio
async def test_list_excludes_password():
    import gateway.dwh_sources as m
    pool = AsyncMock(); pool.fetch = AsyncMock(return_value=[ROW])
    store = m.DwhSourceStore(pool)
    rows = await store.list()
    assert rows and all("password_encrypted" not in r for r in rows)

@pytest.mark.asyncio
async def test_job_lifecycle():
    import gateway.dwh_sources as m
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"id": 7, "status": "running"})
    pool.execute = AsyncMock()
    store = m.DwhSourceStore(pool)
    job = await store.create_job(3); assert job["id"] == 7
    await store.update_job(7, scanned_cols=2, current_table="t"); pool.execute.assert_awaited()
    await store.finish_job(7, "done")
    await store.get_job(7)


@pytest.mark.asyncio
async def test_update_params_contiguous(monkeypatch):
    """Régression : update sans password ne doit pas laisser de trou de placeholder $N."""
    import re
    monkeypatch.setenv("DWH_ENC_KEY", Fernet.generate_key().decode())
    from importlib import reload
    import gateway.dwh_sources as m; reload(m)
    pool = AsyncMock(); pool.fetchrow = AsyncMock(return_value=ROW)
    store = m.DwhSourceStore(pool)
    for pwd in (None, "secret"):
        pool.fetchrow.reset_mock()
        await store.update(1, name="s", db_type="sqlite", host=None, port=None,
                           username=None, password=pwd, options={}, db_filter=[])
        sql, *params = pool.fetchrow.call_args.args
        max_n = max(int(x) for x in re.findall(r"\$(\d+)", sql))
        assert max_n == len(params), f"trou de placeholder pour password={pwd!r}"
        if pwd:
            assert m.decrypt_secret(params[-1]) == "secret"

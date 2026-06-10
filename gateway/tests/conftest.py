"""Stub asyncpg avant tout import — permet de lancer les tests gateway sans Docker.

Même approche que sidecar/tests/conftest.py pour gliner/spacy/presidio.
"""
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

if "asyncpg" not in sys.modules:
    try:
        import asyncpg  # noqa: F401
    except ModuleNotFoundError:
        _asyncpg = ModuleType("asyncpg")
        _asyncpg.create_pool = AsyncMock()
        _asyncpg.Pool = MagicMock()
        sys.modules["asyncpg"] = _asyncpg

if "slowapi" not in sys.modules:
    try:
        import slowapi  # noqa: F401
    except ModuleNotFoundError:
        class _Limiter:
            def __init__(self, key_func=None, **kw):
                self.key_func = key_func

            def limit(self, *_a, **_kw):
                def deco(fn):
                    return fn
                return deco

        class _SlowAPIMiddleware:
            def __init__(self, app, *a, **kw):
                self.app = app

            async def __call__(self, scope, receive, send):
                await self.app(scope, receive, send)

        _slowapi = ModuleType("slowapi")
        _slowapi.Limiter = _Limiter
        sys.modules["slowapi"] = _slowapi
        _mw = ModuleType("slowapi.middleware")
        _mw.SlowAPIMiddleware = _SlowAPIMiddleware
        sys.modules["slowapi.middleware"] = _mw


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=None)
    r.hset = AsyncMock()
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.delete = AsyncMock()
    return r


@pytest.fixture
def mock_db_pool():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def mock_gliner():
    m = MagicMock()
    m.predict_entities = MagicMock(return_value=[])
    return m


@pytest.fixture
def mock_presidio():
    m = MagicMock()
    m.analyze = MagicMock(return_value=[])
    return m

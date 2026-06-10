import pytest
from unittest.mock import AsyncMock
from gateway.column_labels import ColumnLabelStore

@pytest.mark.asyncio
async def test_upsert_ctx_passes_context():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={
        "id": 1, "header_norm": "ref_contrat", "label": "ID", "source": "qwen3",
        "status": "active", "db_name": "dwh", "table_name": "contrats"})
    store = ColumnLabelStore(pool, redis_client=AsyncMock())
    res = await store.upsert_ctx(header="ref_contrat", label="ID", source="qwen3",
                                 confidence=0.9, status="active",
                                 db_name="dwh", table_name="contrats", sample_values=["A", "B"])
    assert res["db_name"] == "dwh" and res["table_name"] == "contrats"
    args = pool.fetchrow.call_args.args      # SQL + positional params
    assert "dwh" in args and "contrats" in args   # contexte transmis au SQL

@pytest.mark.asyncio
async def test_exists_active_true():
    pool = AsyncMock(); pool.fetchrow = AsyncMock(return_value={"col": 1})
    store = ColumnLabelStore(pool, redis_client=AsyncMock())
    assert await store.exists_active("ref", "dwh", "contrats") is True

@pytest.mark.asyncio
async def test_exists_active_false():
    pool = AsyncMock(); pool.fetchrow = AsyncMock(return_value=None)
    store = ColumnLabelStore(pool, redis_client=AsyncMock())
    assert await store.exists_active("ref", "dwh", "contrats") is False

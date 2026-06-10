import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from gateway.auth import validate_api_key, hash_key


def test_hash_key_is_deterministic():
    assert hash_key("anon_abc123") == hash_key("anon_abc123")
    assert len(hash_key("anon_abc123")) == 64


@pytest.mark.asyncio
async def test_validate_api_key_valid(mock_db_pool):
    mock_db_pool.fetchrow.return_value = {"user_id": "uid-123"}
    request = MagicMock()
    request.app.state.db_pool = mock_db_pool
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="anon_validkey")
    user_id = await validate_api_key(request, creds)
    assert user_id == "uid-123"


@pytest.mark.asyncio
async def test_validate_api_key_invalid(mock_db_pool):
    mock_db_pool.fetchrow.return_value = None
    request = MagicMock()
    request.app.state.db_pool = mock_db_pool
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="anon_badkey")
    with pytest.raises(HTTPException) as exc:
        await validate_api_key(request, creds)
    assert exc.value.status_code == 401

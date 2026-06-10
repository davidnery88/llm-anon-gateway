import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from gateway.column_classifier import ColumnClassifier


@pytest.fixture
def classifier():
    return ColumnClassifier(ollama_url="http://mock-ollama:11434")


def _mock_response(strategy: str) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"response": strategy}
    resp.raise_for_status.return_value = None
    return resp


@pytest.mark.asyncio
async def test_classify_returns_label(classifier):
    with patch.object(classifier.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response("mask_name")
        label, _conf, _regex = await classifier.classify("clients", "nom", "varchar", ["David Neri", "Marc Dupont"])
    assert label == "PERSONNE"


@pytest.mark.asyncio
async def test_classify_keep_returns_none(classifier):
    with patch.object(classifier.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response("keep")
        label, _conf, _regex = await classifier.classify("ref", "code_pays", "char", ["CH", "FR", "DE"])
    assert label is None


@pytest.mark.asyncio
async def test_classify_timeout_returns_none(classifier):
    with patch.object(classifier.client, "post", side_effect=httpx.TimeoutException("timeout")):
        label, _conf, _regex = await classifier.classify("t", "col", "varchar", ["x"])
    assert label is None

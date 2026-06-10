"""Tests du garde-fou regex (patterns Qwen avant insertion en KB)."""
from __future__ import annotations

import pytest

from gateway.regex_guard import validate_regex


def test_valid_simple_regex_accepted():
    ok, _ = validate_regex(r"\b756\.\d{4}\.\d{4}\.\d{2}\b")
    assert ok


def test_valid_insurance_policy_regex_accepted():
    ok, _ = validate_regex(r"\bPO\d{9}-\d{5}/\d{2}\b")
    assert ok


def test_invalid_syntax_rejected():
    ok, reason = validate_regex(r"[unclosed")
    assert not ok
    assert "invalide" in reason


def test_empty_regex_rejected():
    ok, _ = validate_regex("")
    assert not ok
    ok, _ = validate_regex("   ")
    assert not ok


def test_nested_unbounded_quantifiers_rejected():
    # Cas d'école ReDoS : backtracking catastrophique
    ok, reason = validate_regex(r"(a+)+b")
    assert not ok
    assert "ReDoS" in reason


def test_nested_star_quantifiers_rejected():
    ok, _ = validate_regex(r"(\d+)*x")
    assert not ok


def test_nested_repeat_in_branch_rejected():
    ok, _ = validate_regex(r"(?:ab|c+d)*z")
    assert not ok


def test_bounded_nested_repeat_accepted():
    # Répétitions bornées : pas de backtracking exponentiel
    ok, _ = validate_regex(r"(\d{2,4}-){1,3}\d{2}")
    assert ok


def test_too_long_regex_rejected():
    ok, reason = validate_regex("a" * 201)
    assert not ok
    assert "long" in reason


# ── Intégration : upsert_pattern_pending refuse les regex dangereux ──────────

from unittest.mock import AsyncMock  # noqa: E402

from gateway.config_store import ConfigStore  # noqa: E402


@pytest.mark.asyncio
async def test_upsert_pattern_pending_rejects_redos_regex():
    pool = AsyncMock()
    store = ConfigStore(pool)
    result = await store.upsert_pattern_pending(
        name="qwen_evil", regex=r"(a+)+b", entity_label="NUM_POLICE", score=0.8
    )
    assert result is None
    pool.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_pattern_pending_rejects_invalid_regex():
    pool = AsyncMock()
    store = ConfigStore(pool)
    result = await store.upsert_pattern_pending(
        name="qwen_bad", regex=r"[unclosed", entity_label="NUM_POLICE", score=0.8
    )
    assert result is None
    pool.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_pattern_pending_accepts_valid_regex():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[None, {"id": 7}])  # pas de doublon, puis INSERT
    store = ConfigStore(pool)
    result = await store.upsert_pattern_pending(
        name="qwen_po", regex=r"\bPO\d{9}-\d{5}/\d{2}\b", entity_label="NUM_POLICE", score=0.8
    )
    assert result is not None
    assert result["id"] == 7
    assert result["source"] == "qwen"

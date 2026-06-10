"""Single source of truth for the slowapi rate limiter.

Lives in its own module so any router (admin, classify, …) can import
the shared instance without circular-importing `gateway.main`.
"""
from __future__ import annotations

from fastapi import Request
from slowapi import Limiter


def _key_from_auth(request: Request) -> str:
    return request.headers.get("Authorization", "").removeprefix("Bearer ")


limiter = Limiter(key_func=_key_from_auth)

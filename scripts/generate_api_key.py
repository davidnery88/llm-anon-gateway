#!/usr/bin/env python3
"""Génère une clé API et l'insère en base PostgreSQL.

Usage:
    python scripts/generate_api_key.py
    python scripts/generate_api_key.py --postgres-dsn postgresql://...
"""
import argparse
import asyncio
import hashlib
import os
import secrets

import asyncpg


def generate_key() -> str:
    return "anon_" + secrets.token_hex(16)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def insert_key(dsn: str, key_hash: str) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "INSERT INTO api_keys (key_hash) VALUES ($1) RETURNING user_id",
            key_hash,
        )
    finally:
        await conn.close()
    return str(row["user_id"])


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a gateway API key")
    parser.add_argument("--postgres-dsn", default=os.environ.get("POSTGRES_DSN"))
    args = parser.parse_args()

    if not args.postgres_dsn:
        raise SystemExit("Set POSTGRES_DSN env var or pass --postgres-dsn")

    key = generate_key()
    user_id = await insert_key(args.postgres_dsn, hash_key(key))

    print(f"\n✓ Clé API générée (affichée une seule fois) :")
    print(f"  {key}")
    print(f"  user_id : {user_id}")
    print(f"\nPour le MCP server, ajouter dans son .env :")
    print(f"  GATEWAY_API_KEY={key}\n")


if __name__ == "__main__":
    asyncio.run(main())

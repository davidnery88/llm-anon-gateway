"""Sources DWH : chiffrement des credentials + persistance (sources + scan_jobs)."""
from __future__ import annotations
import json
import os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get("DWH_ENC_KEY")
    if not key:
        raise RuntimeError("DWH_ENC_KEY manquant : impossible de (dé)chiffrer les credentials DWH")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


class DwhSourceStore:
    def __init__(self, pool):
        self._pool = pool

    async def create(self, *, name, db_type, host, port, username, password,
                     options: dict, db_filter: list[str]) -> dict:
        pw = encrypt_secret(password) if password else None
        row = await self._pool.fetchrow(
            """INSERT INTO dwh_sources
               (name, db_type, host, port, username, password_encrypted, options, db_filter)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               RETURNING id, name, db_type, host, port, username, options, db_filter,
                         last_scan_at, last_scan_status, created_at""",
            name, db_type, host, port, username, pw, json.dumps(options), db_filter,
        )
        return dict(row)

    async def list(self) -> list[dict]:
        rows = await self._pool.fetch(
            """SELECT id, name, db_type, host, port, username, options, db_filter,
                      last_scan_at, last_scan_status, created_at
               FROM dwh_sources ORDER BY name""")
        return [dict(r) for r in rows]

    async def get(self, source_id: int) -> dict | None:
        row = await self._pool.fetchrow("SELECT * FROM dwh_sources WHERE id=$1", source_id)
        return dict(row) if row else None

    async def update(self, source_id: int, *, name, db_type, host, port, username,
                     password, options: dict, db_filter: list[str]) -> dict:
        # Placeholders construits de façon CONTIGUË : si password est vide (cas
        # normal d'édition, write-only), on n'insère pas $N "trou" -> sinon
        # asyncpg échoue sur le param non référencé.
        sets = ["name=$2", "db_type=$3", "host=$4", "port=$5", "username=$6",
                "options=$7", "db_filter=$8"]
        args = [source_id, name, db_type, host, port, username,
                json.dumps(options), db_filter]
        if password:
            args.append(encrypt_secret(password))
            sets.append(f"password_encrypted=${len(args)}")
        row = await self._pool.fetchrow(
            f"""UPDATE dwh_sources SET {', '.join(sets)}
                WHERE id=$1
                RETURNING id, name, db_type, host, port, username, options, db_filter,
                          last_scan_at, last_scan_status, created_at""",
            *args,
        )
        return dict(row)

    async def delete(self, source_id: int) -> None:
        await self._pool.execute("DELETE FROM dwh_sources WHERE id=$1", source_id)

    async def set_last_scan(self, source_id: int, status: str) -> None:
        await self._pool.execute(
            "UPDATE dwh_sources SET last_scan_at=NOW(), last_scan_status=$2 WHERE id=$1",
            source_id, status)

    async def create_job(self, source_id: int) -> dict:
        row = await self._pool.fetchrow(
            "INSERT INTO scan_jobs (source_id) VALUES ($1) RETURNING *", source_id)
        return dict(row)

    async def update_job(self, job_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=${i+2}" for i, k in enumerate(fields))
        await self._pool.execute(
            f"UPDATE scan_jobs SET {cols} WHERE id=$1", job_id, *fields.values())

    async def finish_job(self, job_id: int, status: str, error: str | None = None) -> None:
        await self._pool.execute(
            "UPDATE scan_jobs SET status=$2, error=$3, finished_at=NOW() WHERE id=$1",
            job_id, status, error)

    async def get_job(self, job_id: int) -> dict | None:
        row = await self._pool.fetchrow("SELECT * FROM scan_jobs WHERE id=$1", job_id)
        return dict(row) if row else None

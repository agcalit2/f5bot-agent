import os
import asyncpg
from asyncpg import Pool

_pool: Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        min_size=1,
        max_size=4,
        command_timeout=30,
    )
    async with _pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS seen_emails (uid TEXT PRIMARY KEY)")
        await conn.execute("CREATE TABLE IF NOT EXISTS seen_posts (url TEXT PRIMARY KEY)")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def is_seen_email(uid: str) -> bool:
    async with _pool.acquire() as conn:
        return await conn.fetchval("SELECT 1 FROM seen_emails WHERE uid = $1", uid) is not None


async def mark_seen_email(uid: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO seen_emails (uid) VALUES ($1) ON CONFLICT DO NOTHING", uid
        )


async def is_seen_post(url: str) -> bool:
    async with _pool.acquire() as conn:
        return await conn.fetchval("SELECT 1 FROM seen_posts WHERE url = $1", url) is not None


async def mark_seen_post(url: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO seen_posts (url) VALUES ($1) ON CONFLICT DO NOTHING", url
        )

import asyncpg

from config import get_settings


async def create_pool() -> asyncpg.Pool:
    settings = get_settings()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10)


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()

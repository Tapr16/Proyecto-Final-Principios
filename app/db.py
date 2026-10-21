"""Conexión y pool de conexiones a PostgreSQL."""
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5432/tienda"
)
# Pool acotado: con 3 réplicas son como máximo 3 × (10 + 5) = 45 conexiones,
# por debajo de max_connections=100 de PostgreSQL.
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))

engine = create_async_engine(
    DATABASE_URL,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session


__all__ = ["engine", "SessionLocal", "get_session", "AsyncSession", "DATABASE_URL"]

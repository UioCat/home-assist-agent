"""Async SQLite engine and schema initialization."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from iot_mcp.adapters.outbound.persistence.tables import Base


def create_database_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an engine with SQLite durability and referential-integrity pragmas enabled."""
    engine = create_async_engine(database_url, echo=echo)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def initialize_database(engine: AsyncEngine) -> None:
    """Create the schema after the connection pragmas have been applied."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session

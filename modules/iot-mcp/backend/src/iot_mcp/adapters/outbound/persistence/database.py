"""Async SQLite engine and schema initialization."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
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
        if engine.dialect.name == "sqlite":
            await _migrate_sqlite_schema(connection)


async def _migrate_sqlite_schema(connection: AsyncConnection) -> None:
    await _add_sqlite_columns(
        connection,
        "provider_device_bindings",
        {"provider_id": "VARCHAR(255)"},
    )
    await _add_sqlite_columns(
        connection,
        "control_operations",
        {
            "binding_id": "VARCHAR(36)",
            "provider_id": "VARCHAR(255)",
            "provider_type": "VARCHAR(64)",
            "external_device_ref": "VARCHAR(512)",
            "binding_revision": "INTEGER",
        },
    )
    await _add_sqlite_columns(
        connection,
        "confirmation_requests",
        {
            "binding_id": "VARCHAR(36)",
            "provider_id": "VARCHAR(255)",
            "provider_type": "VARCHAR(64)",
            "external_device_ref": "VARCHAR(512)",
            "binding_revision": "INTEGER NOT NULL DEFAULT 1",
        },
    )
    await connection.execute(
        text(
            "UPDATE provider_device_bindings "
            "SET provider_id = provider_type WHERE provider_id IS NULL"
        )
    )
    await connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_device_provider_binding "
            "ON provider_device_bindings (device_id, provider_id) "
            "WHERE provider_id IS NOT NULL"
        )
    )


async def _add_sqlite_columns(
    connection: AsyncConnection, table: str, columns: dict[str, str]
) -> None:
    result = await connection.execute(text(f"PRAGMA table_info({table})"))
    existing = {row[1] for row in result.all()}
    for name, declaration in columns.items():
        if existing and name not in existing:
            await connection.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            )


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session

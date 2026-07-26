import asyncio
from pathlib import Path
import sqlite3

from home_assist_agent.errors import DependencyError


class SQLiteEventReceiptStore:
    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)

    async def claim(
        self,
        *,
        source: str,
        event_id: str,
        message_id: str,
    ) -> bool:
        try:
            return await asyncio.to_thread(
                self._claim_sync,
                source,
                event_id,
                message_id,
            )
        except (OSError, sqlite3.Error) as error:
            raise DependencyError(
                "event_store_unavailable",
                "事件幂等存储不可用。",
            ) from error

    async def release(
        self,
        *,
        source: str,
        event_id: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._release_sync,
                source,
                event_id,
            )
        except (OSError, sqlite3.Error) as error:
            raise DependencyError(
                "event_store_unavailable",
                "事件幂等存储不可用。",
            ) from error

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=5)
        self._database_path.chmod(0o600)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                source TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(source, source_event_id)
            )
            """
        )
        return connection

    def _claim_sync(
        self,
        source: str,
        event_id: str,
        message_id: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO processed_events (
                    source,
                    source_event_id,
                    message_id
                ) VALUES (?, ?, ?)
                """,
                (source, event_id, message_id),
            )
        return cursor.rowcount == 1

    def _release_sync(self, source: str, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM processed_events
                WHERE source = ? AND source_event_id = ?
                """,
                (source, event_id),
            )

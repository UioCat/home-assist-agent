import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from home_assist_agent.errors import DependencyError
from home_assist_agent.events.models import (
    EventRequest,
    HouseholdContextEntry,
)


class SQLiteHouseholdContextStore:
    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)

    async def upsert(
        self,
        event: EventRequest,
        message_id: str,
    ) -> HouseholdContextEntry:
        try:
            return await asyncio.to_thread(
                self._upsert_sync,
                event,
                message_id,
            )
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise DependencyError(
                "context_unavailable",
                "家庭上下文存储不可用。",
            ) from error

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=5)
        self._database_path.chmod(0o600)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS household_context (
                subject_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                location TEXT,
                attributes_json TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(subject_id, event_type)
            )
            """
        )
        return connection

    def _upsert_sync(
        self,
        event: EventRequest,
        message_id: str,
    ) -> HouseholdContextEntry:
        updated_at = datetime.now(timezone.utc)
        attributes_json = json.dumps(
            event.attributes,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO household_context (
                    subject_id,
                    event_type,
                    location,
                    attributes_json,
                    source_message_id,
                    occurred_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject_id, event_type) DO UPDATE SET
                    location = excluded.location,
                    attributes_json = excluded.attributes_json,
                    source_message_id = excluded.source_message_id,
                    occurred_at = excluded.occurred_at,
                    updated_at = excluded.updated_at
                WHERE excluded.occurred_at >= household_context.occurred_at
                """,
                (
                    event.subject_id,
                    event.event_type,
                    event.location,
                    attributes_json,
                    message_id,
                    event.occurred_at.astimezone(timezone.utc).isoformat(),
                    updated_at.isoformat(),
                ),
            )
            row = connection.execute(
                """
                SELECT
                    subject_id,
                    event_type,
                    location,
                    attributes_json,
                    source_message_id,
                    occurred_at,
                    updated_at
                FROM household_context
                WHERE subject_id = ? AND event_type = ?
                """,
                (event.subject_id, event.event_type),
            ).fetchone()
        return HouseholdContextEntry(
            subject_id=row[0],
            event_type=row[1],
            location=row[2],
            attributes=json.loads(row[3]),
            source_message_id=row[4],
            occurred_at=datetime.fromisoformat(row[5]),
            updated_at=datetime.fromisoformat(row[6]),
        )

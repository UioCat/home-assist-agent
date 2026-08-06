"""Append-only, credential-redacting audit recorder shared by IoT adapters."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(^|_)(authorization|cookie|credential|password|secret|token|api_key)($|_)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(token|api[_-]?key|password|secret)\s*([:=])\s*([^\s,;]+)"
)


class AuditUnavailableError(RuntimeError):
    """Raised when mandatory audit persistence cannot complete."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    message_id: str
    request_id: str
    sequence: int
    event_type: str
    service: str
    payload: Any
    status: str
    error_code: str | None
    created_at: datetime


class AuditRecorder(Protocol):
    async def record(
        self,
        *,
        message_id: str,
        event_type: str,
        service: str,
        payload: Any,
        status: str = "success",
        error_code: str | None = None,
    ) -> AuditEvent: ...


def redact_sensitive(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, BaseModel):
        return redact_sensitive(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_sensitive(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        redacted = _BEARER_VALUE.sub(f"Bearer {REDACTED}", value)
        return _ASSIGNED_SECRET.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
            redacted,
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class SQLiteAuditRecorder:
    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)

    async def record(
        self,
        *,
        message_id: str,
        event_type: str,
        service: str,
        payload: Any,
        status: str = "success",
        error_code: str | None = None,
    ) -> AuditEvent:
        try:
            return await asyncio.to_thread(
                self._record_sync,
                message_id=message_id,
                event_type=event_type,
                service=service,
                payload=redact_sensitive(payload),
                status=status,
                error_code=error_code,
            )
        except AuditUnavailableError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise AuditUnavailableError("audit persistence is unavailable") from error

    async def list_events(self, message_id: str) -> list[AuditEvent]:
        try:
            return await asyncio.to_thread(self._list_events_sync, message_id)
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise AuditUnavailableError("audit persistence is unavailable") from error

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=5)
        self._database_path.chmod(0o600)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema(connection)
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                service TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(message_id, sequence),
                CHECK(request_id = message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_iot_audit_events_message
            ON audit_events(message_id, sequence);

            CREATE TRIGGER IF NOT EXISTS audit_events_no_update
            BEFORE UPDATE ON audit_events
            BEGIN
                SELECT RAISE(ABORT, 'audit_events is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
            BEFORE DELETE ON audit_events
            BEGIN
                SELECT RAISE(ABORT, 'audit_events is append-only');
            END;
            """
        )

    def _record_sync(
        self,
        *,
        message_id: str,
        event_type: str,
        service: str,
        payload: Any,
        status: str,
        error_code: str | None,
    ) -> AuditEvent:
        if not message_id:
            raise ValueError("message_id is required")
        event_id = uuid4().hex
        created_at = datetime.now(UTC)
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM audit_events WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            sequence = int(row[0])
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, message_id, request_id, sequence, event_type,
                    service, payload_json, status, error_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    message_id,
                    message_id,
                    sequence,
                    event_type,
                    service,
                    payload_json,
                    status,
                    error_code,
                    created_at.isoformat(),
                ),
            )
        return AuditEvent(
            event_id=event_id,
            message_id=message_id,
            request_id=message_id,
            sequence=sequence,
            event_type=event_type,
            service=service,
            payload=payload,
            status=status,
            error_code=error_code,
            created_at=created_at,
        )

    def _list_events_sync(self, message_id: str) -> list[AuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, message_id, request_id, sequence, event_type,
                       service, payload_json, status, error_code, created_at
                FROM audit_events
                WHERE message_id = ?
                ORDER BY sequence
                """,
                (message_id,),
            ).fetchall()
        return [
            AuditEvent(
                event_id=row[0],
                message_id=row[1],
                request_id=row[2],
                sequence=row[3],
                event_type=row[4],
                service=row[5],
                payload=json.loads(row[6]),
                status=row[7],
                error_code=row[8],
                created_at=datetime.fromisoformat(row[9]),
            )
            for row in rows
        ]

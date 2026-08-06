import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel

from home_assist_agent.audit.models import AuditEvent, AuditMessageSummary
from home_assist_agent.errors import DependencyError


REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(^|_)(authorization|cookie|credential|password|secret|token|api_key)($|_)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(token|api[_-]?key|password|secret)\s*([:=])\s*([^\s,;]+)"
)


class AuditRecorderProtocol(Protocol):
    async def record(
        self,
        *,
        message_id: str,
        event_type: str,
        service: str,
        payload: Any,
        conversation_id: str | None = None,
        status: str = "success",
        error_code: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AuditEvent: ...


class AuditQueryProtocol(Protocol):
    async def list_messages(
        self,
        limit: int = 50,
    ) -> list[AuditMessageSummary]: ...

    async def list_events(self, message_id: str) -> list[AuditEvent]: ...

    async def list_conversation_events(
        self,
        conversation_id: str,
    ) -> list[AuditEvent]: ...


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
        conversation_id: str | None = None,
        status: str = "success",
        error_code: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AuditEvent:
        try:
            return await asyncio.to_thread(
                self._record_sync,
                message_id=message_id,
                event_type=event_type,
                service=service,
                payload=redact_sensitive(payload),
                conversation_id=conversation_id,
                status=status,
                error_code=error_code,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except DependencyError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise DependencyError(
                "audit_unavailable",
                "审计记录写入失败。",
            ) from error

    async def list_events(self, message_id: str) -> list[AuditEvent]:
        try:
            return await asyncio.to_thread(self._list_events_sync, message_id)
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise DependencyError(
                "audit_unavailable",
                "审计记录读取失败。",
            ) from error

    async def list_conversation_events(
        self,
        conversation_id: str,
    ) -> list[AuditEvent]:
        try:
            return await asyncio.to_thread(
                self._list_conversation_events_sync,
                conversation_id,
            )
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise DependencyError(
                "audit_unavailable",
                "审计记录读取失败。",
            ) from error

    async def list_messages(
        self,
        limit: int = 50,
    ) -> list[AuditMessageSummary]:
        try:
            return await asyncio.to_thread(self._list_messages_sync, limit)
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise DependencyError(
                "audit_unavailable",
                "审计消息读取失败。",
            ) from error

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
                conversation_id TEXT,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                service TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT,
                correlation_id TEXT,
                causation_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(message_id, sequence)
            );

            CREATE INDEX IF NOT EXISTS idx_audit_events_message
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
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(audit_events)").fetchall()
        }
        if "correlation_id" not in columns:
            connection.execute(
                "ALTER TABLE audit_events ADD COLUMN correlation_id TEXT"
            )
        if "causation_id" not in columns:
            connection.execute("ALTER TABLE audit_events ADD COLUMN causation_id TEXT")
        if "conversation_id" not in columns:
            connection.execute(
                "ALTER TABLE audit_events ADD COLUMN conversation_id TEXT"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_events_conversation
                ON audit_events(conversation_id, created_at, event_id)
            """
        )

    def _record_sync(
        self,
        *,
        message_id: str,
        event_type: str,
        service: str,
        payload: Any,
        conversation_id: str | None,
        status: str,
        error_code: str | None,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> AuditEvent:
        event_id = uuid4().hex
        created_at = datetime.now(timezone.utc)
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM audit_events
                WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
            sequence = int(row[0])
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_id,
                    message_id,
                    conversation_id,
                    sequence,
                    event_type,
                    service,
                    payload_json,
                    status,
                    error_code,
                    correlation_id,
                    causation_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    message_id,
                    conversation_id,
                    sequence,
                    event_type,
                    service,
                    payload_json,
                    status,
                    error_code,
                    correlation_id,
                    causation_id,
                    created_at.isoformat(),
                ),
            )
        return AuditEvent(
            event_id=event_id,
            message_id=message_id,
            conversation_id=conversation_id,
            sequence=sequence,
            event_type=event_type,
            service=service,
            payload=payload,
            status=status,
            error_code=error_code,
            correlation_id=correlation_id,
            causation_id=causation_id,
            created_at=created_at,
        )

    def _list_events_sync(self, message_id: str) -> list[AuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    event_id,
                    message_id,
                    conversation_id,
                    sequence,
                    event_type,
                    service,
                    payload_json,
                    status,
                    error_code,
                    correlation_id,
                    causation_id,
                    created_at
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
                conversation_id=row[2],
                sequence=row[3],
                event_type=row[4],
                service=row[5],
                payload=json.loads(row[6]),
                status=row[7],
                error_code=row[8],
                correlation_id=row[9],
                causation_id=row[10],
                created_at=datetime.fromisoformat(row[11]),
            )
            for row in rows
        ]

    def _list_conversation_events_sync(
        self,
        conversation_id: str,
    ) -> list[AuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    event_id,
                    message_id,
                    conversation_id,
                    sequence,
                    event_type,
                    service,
                    payload_json,
                    status,
                    error_code,
                    correlation_id,
                    causation_id,
                    created_at
                FROM audit_events
                WHERE conversation_id = ?
                ORDER BY created_at, event_id
                """,
                (conversation_id,),
            ).fetchall()
        return [
            AuditEvent(
                event_id=row[0],
                message_id=row[1],
                conversation_id=row[2],
                sequence=row[3],
                event_type=row[4],
                service=row[5],
                payload=json.loads(row[6]),
                status=row[7],
                error_code=row[8],
                correlation_id=row[9],
                causation_id=row[10],
                created_at=datetime.fromisoformat(row[11]),
            )
            for row in rows
        ]

    def _list_messages_sync(
        self,
        limit: int,
    ) -> list[AuditMessageSummary]:
        active_limit = max(1, min(limit, 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    message_id,
                    COUNT(*) AS event_count,
                    MIN(created_at) AS started_at,
                    MAX(created_at) AS ended_at,
                    MAX(
                        CASE status
                            WHEN 'error' THEN 2
                            WHEN 'blocked' THEN 1
                            ELSE 0
                        END
                    ) AS status_rank,
                    MAX(correlation_id) AS correlation_id,
                    MAX(conversation_id) AS conversation_id
                FROM audit_events
                GROUP BY message_id
                ORDER BY ended_at DESC
                LIMIT ?
                """,
                (active_limit,),
            ).fetchall()
            if not rows:
                return []

            message_ids = [row[0] for row in rows]
            placeholders = ",".join("?" for _ in message_ids)
            payload_rows = connection.execute(
                f"""
                SELECT message_id, event_type, payload_json, status
                FROM audit_events
                WHERE message_id IN ({placeholders})
                  AND event_type IN (
                      'user.request',
                      'user.response',
                      'event.received',
                      'event.response'
                  )
                ORDER BY sequence
                """,
                message_ids,
            ).fetchall()

        commands: dict[str, str] = {}
        responses: dict[str, str] = {}
        final_statuses: dict[str, str] = {}
        input_types: dict[str, str] = {}
        for message_id, event_type, payload_json, status in payload_rows:
            payload = json.loads(payload_json)
            if event_type == "user.request" and message_id not in commands:
                command = payload.get("command")
                if isinstance(command, str):
                    commands[message_id] = command
                    input_types[message_id] = "message"
            if event_type == "user.response":
                final_statuses[message_id] = status
                response = payload.get("message") or payload.get("error")
                if isinstance(response, str):
                    responses[message_id] = response
            if event_type == "event.received":
                received_type = payload.get("event_type")
                if isinstance(received_type, str):
                    commands[message_id] = f"事件 · {received_type}"
                    input_types[message_id] = "event"
            if event_type == "event.response":
                final_statuses[message_id] = status
                response = payload.get("status")
                if isinstance(response, str):
                    responses[message_id] = response

        statuses = {0: "success", 1: "blocked", 2: "error"}
        return [
            AuditMessageSummary(
                message_id=row[0],
                event_count=row[1],
                started_at=datetime.fromisoformat(row[2]),
                ended_at=datetime.fromisoformat(row[3]),
                status=final_statuses.get(row[0], statuses[row[4]]),
                command=commands.get(row[0]),
                response=responses.get(row[0]),
                input_type=input_types.get(row[0], "message"),
                correlation_id=row[5],
                conversation_id=row[6],
            )
            for row in rows
        ]


class InMemoryAuditRecorder:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        message_id: str,
        event_type: str,
        service: str,
        payload: Any,
        conversation_id: str | None = None,
        status: str = "success",
        error_code: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AuditEvent:
        async with self._lock:
            event = AuditEvent(
                event_id=uuid4().hex,
                message_id=message_id,
                conversation_id=conversation_id,
                sequence=(
                    sum(existing.message_id == message_id for existing in self.events)
                    + 1
                ),
                event_type=event_type,
                service=service,
                payload=redact_sensitive(payload),
                status=status,
                error_code=error_code,
                correlation_id=correlation_id,
                causation_id=causation_id,
                created_at=datetime.now(timezone.utc),
            )
            self.events.append(event)
            return event

    async def list_events(self, message_id: str) -> list[AuditEvent]:
        return [event for event in self.events if event.message_id == message_id]

    async def list_conversation_events(
        self,
        conversation_id: str,
    ) -> list[AuditEvent]:
        return [
            event
            for event in self.events
            if event.conversation_id == conversation_id
        ]

    async def list_messages(
        self,
        limit: int = 50,
    ) -> list[AuditMessageSummary]:
        grouped: dict[str, list[AuditEvent]] = {}
        for event in self.events:
            grouped.setdefault(event.message_id, []).append(event)

        summaries: list[AuditMessageSummary] = []
        for message_id, events in grouped.items():
            command: str | None = None
            response: str | None = None
            final_status: str | None = None
            input_type = "message"
            for event in events:
                if (
                    event.event_type == "user.request"
                    and isinstance(event.payload, dict)
                    and isinstance(event.payload.get("command"), str)
                ):
                    command = event.payload["command"]
                    input_type = "message"
                if event.event_type == "user.response" and isinstance(
                    event.payload, dict
                ):
                    final_status = event.status
                    candidate = event.payload.get("message") or event.payload.get(
                        "error"
                    )
                    if isinstance(candidate, str):
                        response = candidate
                if (
                    event.event_type == "event.received"
                    and isinstance(event.payload, dict)
                    and isinstance(event.payload.get("event_type"), str)
                ):
                    command = f"事件 · {event.payload['event_type']}"
                    input_type = "event"
                if event.event_type == "event.response" and isinstance(
                    event.payload, dict
                ):
                    final_status = event.status
                    candidate = event.payload.get("status")
                    if isinstance(candidate, str):
                        response = candidate
            statuses = {event.status for event in events}
            fallback_status = (
                "error"
                if "error" in statuses
                else "blocked"
                if "blocked" in statuses
                else "success"
            )
            summaries.append(
                AuditMessageSummary(
                    message_id=message_id,
                    command=command,
                    response=response,
                    input_type=input_type,
                    correlation_id=next(
                        (
                            event.correlation_id
                            for event in reversed(events)
                            if event.correlation_id is not None
                        ),
                        None,
                    ),
                    conversation_id=next(
                        (
                            event.conversation_id
                            for event in reversed(events)
                            if event.conversation_id is not None
                        ),
                        None,
                    ),
                    status=final_status or fallback_status,
                    event_count=len(events),
                    started_at=min(event.created_at for event in events),
                    ended_at=max(event.created_at for event in events),
                )
            )
        summaries.sort(key=lambda item: item.ended_at, reverse=True)
        return summaries[: max(1, min(limit, 100))]

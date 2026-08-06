import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from home_assist_agent.errors import DependencyError


@dataclass(frozen=True, slots=True)
class ConversationThread:
    conversation_id: str
    home_id: str
    person_id: str
    codex_thread_id: str | None
    status: str
    created_at: datetime
    last_used_at: datetime


@dataclass(frozen=True, slots=True)
class MessageReceipt:
    message_id: str
    conversation_id: str
    channel: str
    command: str
    status: str
    response: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None
    is_new: bool = False


class SQLiteConversationStore:
    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)

    async def resolve_active(
        self,
        home_id: str,
        person_id: str,
    ) -> ConversationThread:
        return await self._run(self._resolve_active_sync, home_id, person_id)

    async def bind_thread(
        self,
        conversation_id: str,
        codex_thread_id: str,
    ) -> ConversationThread:
        return await self._run(
            self._bind_thread_sync,
            conversation_id,
            codex_thread_id,
        )

    async def create_new(
        self,
        home_id: str,
        person_id: str,
    ) -> ConversationThread:
        return await self._run(self._create_new_sync, home_id, person_id)

    async def get(self, conversation_id: str) -> ConversationThread | None:
        return await self._run(self._get_sync, conversation_id)

    async def claim_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        channel: str,
        command: str,
    ) -> MessageReceipt:
        return await self._run(
            self._claim_message_sync,
            message_id,
            conversation_id,
            channel,
            command,
        )

    async def complete_message(
        self,
        message_id: str,
        response: dict[str, Any],
    ) -> MessageReceipt:
        return await self._run(
            self._complete_message_sync,
            message_id,
            response,
        )

    async def list_messages(
        self,
        conversation_id: str,
    ) -> list[MessageReceipt]:
        return await self._run(self._list_messages_sync, conversation_id)

    async def fail_message(self, message_id: str) -> MessageReceipt:
        return await self._run(self._fail_message_sync, message_id)

    async def _run(self, operation, *args):
        try:
            return await asyncio.to_thread(operation, *args)
        except DependencyError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise DependencyError(
                "conversation_store_unavailable",
                "会话存储暂时不可用。",
            ) from error

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        self._database_path.chmod(0o600)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_threads (
                conversation_id TEXT PRIMARY KEY,
                home_id TEXT NOT NULL,
                person_id TEXT NOT NULL,
                codex_thread_id TEXT UNIQUE,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_owner_status
                ON conversation_threads(home_id, person_id, status, last_used_at);

            CREATE TABLE IF NOT EXISTS message_receipts (
                message_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                external_message_id TEXT,
                command TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(conversation_id)
                    REFERENCES conversation_threads(conversation_id)
            );

            CREATE INDEX IF NOT EXISTS idx_message_receipts_conversation
                ON message_receipts(conversation_id, created_at);
            """
        )
        return connection

    def _resolve_active_sync(
        self,
        home_id: str,
        person_id: str,
    ) -> ConversationThread:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM conversation_threads
                WHERE home_id = ? AND person_id = ?
                  AND status IN ('creating', 'active')
                ORDER BY last_used_at DESC
                LIMIT 1
                """,
                (home_id, person_id),
            ).fetchone()
            if row is None:
                now = datetime.now(UTC).isoformat()
                conversation_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO conversation_threads (
                        conversation_id, home_id, person_id, codex_thread_id,
                        status, created_at, last_used_at, revision
                    ) VALUES (?, ?, ?, NULL, 'creating', ?, ?, 0)
                    """,
                    (conversation_id, home_id, person_id, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM conversation_threads WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
        return self._thread_from_row(row)

    def _bind_thread_sync(
        self,
        conversation_id: str,
        codex_thread_id: str,
    ) -> ConversationThread:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM conversation_threads WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("conversation does not exist")
            existing_thread_id = row["codex_thread_id"]
            if existing_thread_id not in (None, codex_thread_id):
                raise ValueError("conversation already has another thread")
            connection.execute(
                """
                UPDATE conversation_threads
                SET codex_thread_id = ?, status = 'active',
                    last_used_at = ?, revision = revision + 1
                WHERE conversation_id = ?
                """,
                (codex_thread_id, now, conversation_id),
            )
            row = connection.execute(
                "SELECT * FROM conversation_threads WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return self._thread_from_row(row)

    def _create_new_sync(
        self,
        home_id: str,
        person_id: str,
    ) -> ConversationThread:
        now = datetime.now(UTC).isoformat()
        conversation_id = uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE conversation_threads
                SET status = 'closed', last_used_at = ?, revision = revision + 1
                WHERE home_id = ? AND person_id = ?
                  AND status IN ('creating', 'active')
                """,
                (now, home_id, person_id),
            )
            connection.execute(
                """
                INSERT INTO conversation_threads (
                    conversation_id, home_id, person_id, codex_thread_id,
                    status, created_at, last_used_at, revision
                ) VALUES (?, ?, ?, NULL, 'creating', ?, ?, 0)
                """,
                (conversation_id, home_id, person_id, now, now),
            )
            row = connection.execute(
                "SELECT * FROM conversation_threads WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return self._thread_from_row(row)

    def _get_sync(self, conversation_id: str) -> ConversationThread | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_threads WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return None if row is None else self._thread_from_row(row)

    def _claim_message_sync(
        self,
        message_id: str,
        conversation_id: str,
        channel: str,
        command: str,
    ) -> MessageReceipt:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM message_receipts WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            is_new = row is None
            if row is None:
                connection.execute(
                    """
                    INSERT INTO message_receipts (
                        message_id, request_id, conversation_id, channel,
                        external_message_id, command, status, response_json,
                        created_at, completed_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, 'processing', NULL, ?, NULL)
                    """,
                    (
                        message_id,
                        message_id,
                        conversation_id,
                        channel,
                        command,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM message_receipts WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
        return self._receipt_from_row(row, is_new=is_new)

    def _complete_message_sync(
        self,
        message_id: str,
        response: dict[str, Any],
    ) -> MessageReceipt:
        now = datetime.now(UTC).isoformat()
        response_json = json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE message_receipts
                SET status = 'completed', response_json = ?, completed_at = ?
                WHERE message_id = ? AND status = 'processing'
                """,
                (response_json, now, message_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("message receipt is not processing")
            row = connection.execute(
                "SELECT * FROM message_receipts WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return self._receipt_from_row(row)

    def _list_messages_sync(
        self,
        conversation_id: str,
    ) -> list[MessageReceipt]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM message_receipts
                WHERE conversation_id = ?
                ORDER BY created_at, message_id
                """,
                (conversation_id,),
            ).fetchall()
        return [self._receipt_from_row(row) for row in rows]

    def _fail_message_sync(self, message_id: str) -> MessageReceipt:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE message_receipts
                SET status = 'failed', completed_at = ?
                WHERE message_id = ? AND status = 'processing'
                """,
                (now, message_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("message receipt is not processing")
            row = connection.execute(
                "SELECT * FROM message_receipts WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return self._receipt_from_row(row)

    @staticmethod
    def _thread_from_row(row: sqlite3.Row) -> ConversationThread:
        return ConversationThread(
            conversation_id=row["conversation_id"],
            home_id=row["home_id"],
            person_id=row["person_id"],
            codex_thread_id=row["codex_thread_id"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_used_at=datetime.fromisoformat(row["last_used_at"]),
        )

    @staticmethod
    def _receipt_from_row(
        row: sqlite3.Row,
        *,
        is_new: bool = False,
    ) -> MessageReceipt:
        response_json = row["response_json"]
        return MessageReceipt(
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            channel=row["channel"],
            command=row["command"],
            status=row["status"],
            response=(
                json.loads(response_json) if response_json is not None else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
            is_new=is_new,
        )

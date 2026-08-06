import json
from pathlib import Path
import sqlite3

import pytest

from home_assist_agent.audit.recorder import SQLiteAuditRecorder


@pytest.mark.asyncio
async def test_sqlite_audit_recorder_appends_ordered_events_by_message_id(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audit.db"
    recorder = SQLiteAuditRecorder(database_path)

    await recorder.record(
        message_id="message-1",
        conversation_id="conversation-1",
        event_type="user.request",
        service="web",
        payload={"command": "打开客厅灯"},
        correlation_id="conversation-1",
        causation_id="source-1",
    )
    await recorder.record(
        message_id="message-1",
        event_type="user.response",
        service="web",
        payload={"message": "已打开"},
    )
    await recorder.record(
        message_id="message-2",
        event_type="user.request",
        service="web",
        payload={"command": "你好"},
    )

    events = await recorder.list_events("message-1")

    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == [
        "user.request",
        "user.response",
    ]
    assert events[0].payload == {"command": "打开客厅灯"}
    assert events[0].correlation_id == "conversation-1"
    assert events[0].causation_id == "source-1"
    assert all(event.message_id == "message-1" for event in events)

    messages = await recorder.list_messages()
    assert [message.message_id for message in messages] == [
        "message-2",
        "message-1",
    ]
    assert messages[1].command == "打开客厅灯"
    assert messages[1].response == "已打开"
    assert messages[1].event_count == 2
    assert messages[1].status == "success"
    assert messages[1].correlation_id == "conversation-1"
    assert messages[1].conversation_id == "conversation-1"


@pytest.mark.asyncio
async def test_audit_events_can_be_queried_in_conversation_order(
    tmp_path: Path,
) -> None:
    recorder = SQLiteAuditRecorder(tmp_path / "audit.db")
    await recorder.record(
        message_id="message-1",
        conversation_id="conversation-1",
        event_type="user.request",
        service="console",
        payload={"command": "打开灯"},
    )
    await recorder.record(
        message_id="message-2",
        conversation_id="conversation-1",
        event_type="user.response",
        service="voice",
        payload={"message": "已打开"},
    )
    await recorder.record(
        message_id="message-other",
        conversation_id="conversation-2",
        event_type="user.request",
        service="console",
        payload={"command": "你好"},
    )

    events = await recorder.list_conversation_events("conversation-1")

    assert [event.message_id for event in events] == ["message-1", "message-2"]
    assert all(event.request_id == event.message_id for event in events)
    assert {event.conversation_id for event in events} == {"conversation-1"}


@pytest.mark.asyncio
async def test_audit_recorder_redacts_credentials_before_persistence(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audit.db"
    recorder = SQLiteAuditRecorder(database_path)

    await recorder.record(
        message_id="message-secret",
        event_type="external.request",
        service="home_assistant_mcp",
        payload={
            "headers": {"Authorization": "Bearer top-secret"},
            "ha_token": "top-secret",
            "description": "token=top-secret",
            "arguments": {"name": "客厅灯"},
        },
    )

    events = await recorder.list_events("message-secret")
    serialized = json.dumps(events[0].payload, ensure_ascii=False)

    assert "top-secret" not in serialized
    assert serialized.count("[REDACTED]") >= 3
    assert events[0].payload["arguments"] == {"name": "客厅灯"}


@pytest.mark.asyncio
async def test_message_summary_uses_final_user_response_status(
    tmp_path: Path,
) -> None:
    recorder = SQLiteAuditRecorder(tmp_path / "audit.db")
    await recorder.record(
        message_id="message-partial",
        event_type="external.response",
        service="home_assistant_mcp",
        payload={"error": "HA 未配置"},
        status="error",
        error_code="ha_not_configured",
    )
    await recorder.record(
        message_id="message-partial",
        event_type="user.response",
        service="web",
        payload={"message": "仍然可以回答普通问题"},
        status="success",
    )

    messages = await recorder.list_messages()

    assert messages[0].status == "success"
    assert messages[0].response == "仍然可以回答普通问题"


@pytest.mark.asyncio
async def test_audit_table_rejects_updates_and_deletes(tmp_path: Path) -> None:
    database_path = tmp_path / "audit.db"
    recorder = SQLiteAuditRecorder(database_path)
    await recorder.record(
        message_id="message-immutable",
        event_type="user.request",
        service="web",
        payload={"command": "你好"},
    )

    connection = sqlite3.connect(database_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE audit_events SET status = 'error' WHERE message_id = ?",
                ("message-immutable",),
            )
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "DELETE FROM audit_events WHERE message_id = ?",
                ("message-immutable",),
            )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_existing_audit_database_migrates_correlation_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audit.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TABLE audit_events (
                event_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                service TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(message_id, sequence)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                event_id,
                message_id,
                sequence,
                event_type,
                service,
                payload_json,
                status,
                error_code,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-event",
                "legacy-message",
                1,
                "user.request",
                "web",
                '{"command":"你好"}',
                "success",
                None,
                "2026-07-26T08:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    events = await SQLiteAuditRecorder(database_path).list_events("legacy-message")

    assert len(events) == 1
    assert events[0].correlation_id is None
    assert events[0].causation_id is None

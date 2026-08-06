import json
import sqlite3

import pytest

from iot_mcp.audit import REDACTED, SQLiteAuditRecorder


@pytest.mark.asyncio
async def test_audit_recorder_appends_ordered_redacted_events(tmp_path) -> None:
    database = tmp_path / "audit.db"
    recorder = SQLiteAuditRecorder(database)

    await recorder.record(
        message_id="message-1",
        event_type="external.request",
        service="home_assistant",
        payload={
            "authorization": "Bearer hidden-token",
            "nested": {"password": "hidden-password"},
            "operation": "states",
        },
    )
    await recorder.record(
        message_id="message-1",
        event_type="external.response",
        service="home_assistant",
        payload={"status_code": 200, "body": [{"entity_id": "light.desk"}]},
    )

    events = await recorder.list_events("message-1")

    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == [
        "external.request",
        "external.response",
    ]
    serialized = json.dumps([event.payload for event in events])
    assert "hidden-token" not in serialized
    assert "hidden-password" not in serialized
    assert REDACTED in serialized


@pytest.mark.asyncio
async def test_audit_recorder_history_rejects_updates_and_deletes(tmp_path) -> None:
    database = tmp_path / "audit.db"
    recorder = SQLiteAuditRecorder(database)
    await recorder.record(
        message_id="message-immutable",
        event_type="system.request",
        service="device_sync",
        payload={"provider_id": "home_assistant"},
    )

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("UPDATE audit_events SET status = 'error'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM audit_events")

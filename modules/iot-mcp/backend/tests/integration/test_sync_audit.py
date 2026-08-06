import sqlite3

import pytest

from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.audit import AuditUnavailableError, SQLiteAuditRecorder
from iot_mcp.bootstrap.container import create_container
from iot_mcp.config.settings import Settings


@pytest.mark.asyncio
async def test_startup_sync_uses_one_message_id_for_append_only_audit(tmp_path) -> None:
    audit_database = tmp_path / "audit.db"
    container = create_container(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'iot.db'}",
            mock_provider_enabled=False,
        ),
        providers={"mock": MockDeviceProvider()},
        audit=SQLiteAuditRecorder(audit_database),
    )

    await container.startup()
    await container.shutdown()

    with sqlite3.connect(audit_database) as connection:
        rows = connection.execute(
            """
            SELECT message_id, request_id, event_type, service
            FROM audit_events
            WHERE service = 'device_sync'
            ORDER BY sequence
            """
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == rows[1][0]
    assert all(message_id == request_id for message_id, request_id, _, _ in rows)
    assert [row[2] for row in rows] == ["system.request", "system.response"]


class FailingAuditRecorder:
    async def record(self, **_: object) -> None:
        raise AuditUnavailableError("audit unavailable")


class CountingMockProvider(MockDeviceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.discover_calls = 0

    async def discover(self, *, message_id: str):
        self.discover_calls += 1
        return await super().discover(message_id=message_id)


@pytest.mark.asyncio
async def test_startup_sync_does_not_call_provider_when_audit_is_unavailable(tmp_path) -> None:
    provider = CountingMockProvider()
    container = create_container(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'iot.db'}",
            mock_provider_enabled=False,
        ),
        providers={provider.provider_id: provider},
        audit=FailingAuditRecorder(),
    )

    await container.startup()
    await container.shutdown()

    assert provider.discover_calls == 0
    assert container.provider_status == {provider.provider_id: "degraded"}

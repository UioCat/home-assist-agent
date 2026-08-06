
import pytest
from httpx import ASGITransport, AsyncClient

from iot_mcp.adapters.inbound.http.app import create_app
from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.audit import AuditUnavailableError, SQLiteAuditRecorder
from iot_mcp.bootstrap.container import create_container
from iot_mcp.config.settings import Settings


@pytest.mark.asyncio
async def test_http_request_and_final_response_share_message_and_request_id(tmp_path) -> None:
    audit = SQLiteAuditRecorder(tmp_path / "audit.db")
    container = create_container(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'iot.db'}",
            mock_provider_enabled=False,
        ),
        providers={"mock": MockDeviceProvider()},
        audit=audit,
    )
    app = create_app(container=container)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/providers")

    assert response.status_code == 200
    message_id = response.headers["X-Message-ID"]
    assert response.headers["X-Request-ID"] == message_id
    events = [
        event
        for event in await audit.list_events(message_id)
        if event.service == "http_api"
    ]
    assert [event.event_type for event in events] == [
        "user.request",
        "user.response",
    ]
    assert events[0].payload == {
        "method": "GET",
        "path": "/api/v1/providers",
        "query": "",
        "body": None,
    }
    assert events[1].payload["status_code"] == 200
    assert events[1].payload["body"] == response.json()
    assert all(event.request_id == message_id for event in events)


class FailingAuditRecorder:
    async def record(self, **_: object) -> None:
        raise AuditUnavailableError("audit unavailable")


class CountingHealthProvider(MockDeviceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.health_calls = 0

    async def health(self, *, message_id: str | None = None):
        self.health_calls += 1
        return await super().health(message_id=message_id)


@pytest.mark.asyncio
async def test_http_route_is_blocked_before_execution_when_audit_is_unavailable(
    tmp_path,
) -> None:
    provider = CountingHealthProvider()
    container = create_container(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'iot.db'}",
            mock_provider_enabled=False,
        ),
        providers={provider.provider_id: provider},
        audit=FailingAuditRecorder(),
    )
    app = create_app(container=container)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/providers")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "audit_unavailable"
    assert body["error"]["message_id"] == body["error"]["request_id"]
    assert response.headers["X-Message-ID"] == body["error"]["message_id"]
    assert provider.health_calls == 0

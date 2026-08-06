import httpx
import pytest

from iot_mcp.adapters.outbound.home_assistant.client import HomeAssistantClient
from iot_mcp.audit import AuditUnavailableError, SQLiteAuditRecorder


@pytest.mark.asyncio
async def test_ha_client_audits_request_and_response_with_one_message_id(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-token"
        return httpx.Response(
            200,
            json=[{"entity_id": "light.desk", "state": "on", "attributes": {}}],
        )

    audit = SQLiteAuditRecorder(tmp_path / "audit.db")
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = HomeAssistantClient(
        "http://ha.local:8123",
        "secret-token",
        client=http_client,
        audit=audit,
    )

    states = await client.get_states(message_id="message-ha-states")
    await client.aclose()

    assert states[0]["entity_id"] == "light.desk"
    events = await audit.list_events("message-ha-states")
    assert [event.event_type for event in events] == [
        "external.request",
        "external.response",
    ]
    assert all(event.service == "home_assistant" for event in events)
    assert events[0].payload == {
        "method": "GET",
        "path": "/api/states",
        "json": None,
    }
    assert events[1].payload["status_code"] == 200
    assert events[1].payload["body"][0]["entity_id"] == "light.desk"


class FailingAuditRecorder:
    async def record(self, **_: object) -> None:
        raise AuditUnavailableError("audit unavailable")


@pytest.mark.asyncio
async def test_ha_client_blocks_network_when_request_audit_is_unavailable() -> None:
    network_calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        return httpx.Response(200, json=[])

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = HomeAssistantClient(
        "http://ha.local:8123",
        "secret-token",
        client=http_client,
        audit=FailingAuditRecorder(),
    )

    with pytest.raises(AuditUnavailableError):
        await client.get_states(message_id="message-audit-failure")
    await client.aclose()

    assert network_calls == 0

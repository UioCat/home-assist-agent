from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
from typing import Any, AsyncIterator

import httpx
import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.errors import DependencyError
from home_assist_agent.ha.catalog import HomeAssistantCatalogClient
from home_assist_agent.resolution.models import ActorContext


ACTOR = ActorContext(home_id="home-1", person_id="person-1")


def states_payload(*, state: str = "off", friendly_name: str = "左侧台灯"):
    return [
        {
            "entity_id": "light.bedroom_left",
            "state": state,
            "attributes": {
                "friendly_name": friendly_name,
                "supported_color_modes": ["brightness"],
            },
            "last_changed": "2026-07-28T01:00:00+00:00",
            "last_updated": "2026-07-28T01:00:00+00:00",
        }
    ]


ENTITY_REGISTRY = [
    {
        "entity_id": "light.bedroom_left",
        "name": None,
        "original_name": "Bedside lamp",
        "aliases": ["床头灯"],
        "device_id": "device-1",
        "area_id": None,
        "disabled_by": None,
        "hidden_by": None,
    }
]
DEVICE_REGISTRY = [
    {
        "id": "device-1",
        "name": "Bedside device",
        "name_by_user": "左床头灯",
        "aliases": ["睡觉旁边的灯"],
        "area_id": "bedroom",
    }
]
AREA_REGISTRY = [
    {
        "area_id": "bedroom",
        "name": "卧室",
        "aliases": ["睡房"],
        "floor_id": None,
    }
]


def states_transport(
    payload: Any | None = None,
    *,
    status_code: int = 200,
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/states"
        assert request.headers["Authorization"] == "Bearer top-secret"
        return httpx.Response(
            status_code,
            json=states_payload() if payload is None else payload,
            request=request,
        )

    return httpx.MockTransport(handler)


@dataclass
class FakeRegistrySocket:
    results: dict[str, Any]
    requests: list[dict[str, Any]] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)

    async def send(self, value: str) -> None:
        request = json.loads(value)
        self.requests.append(request)
        self.pending.append(request["type"])

    async def recv(self) -> str:
        operation = self.pending.pop(0)
        result = self.results[operation]
        if isinstance(result, Exception):
            raise result
        return json.dumps(
            {
                "id": len(self.requests),
                "type": "result",
                "success": True,
                "result": result,
            }
        )


def registry_factory_for(socket: FakeRegistrySocket):
    @asynccontextmanager
    async def factory(
        url: str,
        token: str,
        timeout_seconds: float,
    ) -> AsyncIterator[FakeRegistrySocket]:
        assert url == "ws://ha.local:8123/api/websocket"
        assert token == "top-secret"
        assert timeout_seconds == 3
        yield socket

    return factory


def registry_socket(
    *,
    entities: Any = ENTITY_REGISTRY,
    devices: Any = DEVICE_REGISTRY,
    areas: Any = AREA_REGISTRY,
) -> FakeRegistrySocket:
    return FakeRegistrySocket(
        {
            "config/entity_registry/list": entities,
            "config/device_registry/list": devices,
            "config/area_registry/list": areas,
        }
    )


def catalog_client(
    *,
    audit: InMemoryAuditRecorder | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    socket: FakeRegistrySocket | None = None,
) -> tuple[
    HomeAssistantCatalogClient,
    InMemoryAuditRecorder,
    FakeRegistrySocket,
]:
    active_audit = audit or InMemoryAuditRecorder()
    active_socket = socket or registry_socket()
    return (
        HomeAssistantCatalogClient(
            base_url="http://ha.local:8123",
            token="top-secret",
            timeout_seconds=3,
            http_transport=transport or states_transport(),
            websocket_factory=registry_factory_for(active_socket),
            audit=active_audit,
        ),
        active_audit,
        active_socket,
    )


@pytest.mark.asyncio
async def test_catalog_merges_states_and_registries_without_auditing_token() -> None:
    client, audit, socket = catalog_client()

    snapshot = await client.snapshot(ACTOR, "message-1")

    assert snapshot.home_id == "home-1"
    assert len(snapshot.entities) == 1
    entity = snapshot.entities[0]
    assert entity.entity_id == "light.bedroom_left"
    assert entity.aliases == ("床头灯",)
    assert entity.device_name == "左床头灯"
    assert entity.device_aliases == ("睡觉旁边的灯",)
    assert entity.area_id == "bedroom"
    assert entity.area_name == "卧室"
    assert entity.capabilities == frozenset(
        {"turn_on", "turn_off", "set_brightness"}
    )
    assert [request["type"] for request in socket.requests] == [
        "config/entity_registry/list",
        "config/device_registry/list",
        "config/area_registry/list",
    ]
    serialized_audit = json.dumps(
        [event.payload for event in audit.events],
        ensure_ascii=False,
    )
    assert "top-secret" not in serialized_audit


@pytest.mark.asyncio
async def test_catalog_records_complete_business_request_response_order() -> None:
    client, audit, _ = catalog_client()

    await client.snapshot(
        ACTOR,
        "message-audit",
        correlation_id="correlation-1",
        causation_id="cause-1",
    )

    assert [event.event_type for event in audit.events] == [
        "external.request",
        "external.response",
        "external.request",
        "external.response",
        "external.request",
        "external.response",
        "external.request",
        "external.response",
    ]
    assert [
        event.payload["operation"]
        for event in audit.events
        if event.event_type == "external.request"
    ] == [
        "states",
        "entity_registry",
        "device_registry",
        "area_registry",
    ]
    assert all(event.correlation_id == "correlation-1" for event in audit.events)
    assert all(event.causation_id == "cause-1" for event in audit.events)
    response_payloads = [
        event.payload["response"]
        for event in audit.events
        if event.event_type == "external.response"
    ]
    assert response_payloads[0] == states_payload()
    assert response_payloads[1] == ENTITY_REGISTRY


@pytest.mark.asyncio
async def test_state_only_change_preserves_catalog_version() -> None:
    first, _, _ = catalog_client(transport=states_transport(states_payload(state="off")))
    second, _, _ = catalog_client(
        transport=states_transport(states_payload(state="on"))
    )

    first_snapshot = await first.snapshot(ACTOR, "message-first")
    second_snapshot = await second.snapshot(ACTOR, "message-second")

    assert first_snapshot.catalog_version == second_snapshot.catalog_version
    assert first_snapshot.entities[0].state == "off"
    assert second_snapshot.entities[0].state == "on"


@pytest.mark.asyncio
async def test_identity_change_changes_catalog_version() -> None:
    first, _, _ = catalog_client()
    second, _, _ = catalog_client(
        transport=states_transport(
            states_payload(friendly_name="右侧台灯")
        )
    )

    first_snapshot = await first.snapshot(ACTOR, "message-first")
    second_snapshot = await second.snapshot(ACTOR, "message-second")

    assert first_snapshot.catalog_version != second_snapshot.catalog_version


@pytest.mark.asyncio
async def test_disabled_registry_entity_is_retained_but_not_available() -> None:
    disabled_registry = [
        {
            **ENTITY_REGISTRY[0],
            "entity_id": "light.disabled",
            "disabled_by": "user",
        }
    ]
    client, _, _ = catalog_client(
        transport=states_transport(payload=[]),
        socket=registry_socket(entities=disabled_registry),
    )

    snapshot = await client.snapshot(ACTOR, "message-disabled")

    assert snapshot.entities[0].entity_id == "light.disabled"
    assert snapshot.entities[0].disabled is True
    assert snapshot.entities[0].available is False


@pytest.mark.asyncio
async def test_http_unauthorized_is_mapped_and_audited() -> None:
    client, audit, _ = catalog_client(
        transport=states_transport(payload={"message": "Unauthorized"}, status_code=401)
    )

    with pytest.raises(DependencyError) as captured:
        await client.snapshot(ACTOR, "message-401")

    assert captured.value.code == "ha_unauthorized"
    assert [event.event_type for event in audit.events] == [
        "external.request",
        "external.response",
    ]
    assert audit.events[-1].status == "error"
    assert audit.events[-1].error_code == "ha_unauthorized"


@pytest.mark.asyncio
async def test_timeout_is_mapped_and_audited() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client, audit, _ = catalog_client(
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(DependencyError) as captured:
        await client.snapshot(ACTOR, "message-timeout")

    assert captured.value.code == "ha_unavailable"
    assert audit.events[-1].error_code == "ha_unavailable"


@pytest.mark.asyncio
async def test_malformed_registry_response_is_rejected_and_audited() -> None:
    client, audit, _ = catalog_client(
        socket=registry_socket(entities={"not": "a list"}),
    )

    with pytest.raises(DependencyError) as captured:
        await client.snapshot(ACTOR, "message-malformed")

    assert captured.value.code == "ha_invalid_response"
    assert audit.events[-1].event_type == "external.response"
    assert audit.events[-1].payload["operation"] == "entity_registry"
    assert audit.events[-1].status == "error"

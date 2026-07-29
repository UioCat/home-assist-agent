import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from iot_mcp.adapters.outbound.home_assistant.client import HomeAssistantClient
from iot_mcp.adapters.outbound.home_assistant.provider import HomeAssistantDeviceProvider
from iot_mcp.adapters.outbound.persistence.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from iot_mcp.adapters.outbound.persistence.repositories import (
    DeviceRepository,
    StateRepository,
    ThingModelRepository,
)
from iot_mcp.application.sync_service import DeviceSyncService
from iot_mcp.ports.device_provider import ProviderEvent, ProviderInventory


def _transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    states = {
        "light.desk": {"entity_id": "light.desk", "state": "on", "attributes": {"brightness": 128}},
        "climate.living": {
            "entity_id": "climate.living",
            "state": "heat",
            "attributes": {"current_temperature": 20.0, "temperature": 22.0},
        },
        "lock.front": {"entity_id": "lock.front", "state": "locked", "attributes": {}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/states":
            return httpx.Response(200, json=list(states.values()))
        if request.url.path.startswith("/api/states/"):
            return httpx.Response(200, json=states[request.url.path.rsplit("/", 1)[-1]])
        if request.url.path == "/api/services/light/turn_on":
            body = json.loads(request.content)
            states["light.desk"]["state"] = "on"
            states["light.desk"]["attributes"]["brightness"] = body["brightness"]
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "missing"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_ha_provider_controls_only_through_services_and_reads_before_after() -> None:
    calls: list[httpx.Request] = []
    client = HomeAssistantClient(
        "http://ha.test", "not-a-real-token", client=httpx.AsyncClient(transport=_transport(calls))
    )
    provider = HomeAssistantDeviceProvider(client)
    inventory = await provider.discover()
    light = next(
        device for device in inventory.devices if device.external_ref == "entity:light.desk"
    )

    result = await provider.write_properties(light.external_ref, {"Brightness": 100})

    assert result.ok
    assert result.data["before"]["Brightness"] == 50
    assert result.data["after"]["Brightness"] == 100
    assert [request.url.path for request in calls].count("/api/states/light.desk") == 2
    assert "/api/services/light/turn_on" in [request.url.path for request in calls]
    assert not any(
        "/api/states/light.desk" == request.url.path and request.method == "POST"
        for request in calls
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_sync_upserts_and_marks_missing_and_refreshes_snapshots(tmp_path) -> None:
    calls: list[httpx.Request] = []
    client = HomeAssistantClient(
        "http://ha.test", "not-a-real-token", client=httpx.AsyncClient(transport=_transport(calls))
    )
    provider = HomeAssistantDeviceProvider(client)
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'iot.db'}")
    await initialize_database(engine)
    sessions = create_session_factory(engine)
    device_repo = DeviceRepository(sessions)
    service = DeviceSyncService(
        provider, ThingModelRepository(sessions), device_repo, StateRepository(sessions)
    )

    first = await service.sync()
    second = await service.sync()
    devices = await device_repo.list_devices()

    assert first.discovered == second.discovered == 3
    assert first.upserted == 3
    assert all(device.status == "active" for device in devices)
    assert all([await device_repo.list_bindings(device.device_id) for device in devices])
    await client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_ha_provider_subscription_accepts_an_injected_fake_event_source() -> None:
    async def fake_events() -> AsyncIterator[dict[str, object]]:
        yield {"event": {"data": {"entity_id": "light.desk"}}}

    client = HomeAssistantClient(
        "http://ha.test", "not-a-real-token", client=httpx.AsyncClient(transport=_transport([]))
    )
    provider = HomeAssistantDeviceProvider(client, event_source=fake_events)
    received: list[ProviderEvent] = []
    subscription = await provider.subscribe(received.append)

    for _ in range(10):
        if received:
            break
        await asyncio.sleep(0)

    assert received[0].device_ref == "entity:light.desk"
    assert received[0].identifier == "state_changed"
    await subscription.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_ha_registry_resolver_groups_entities_under_one_physical_device() -> None:
    calls: list[httpx.Request] = []
    client = HomeAssistantClient(
        "http://ha.test", "not-a-real-token", client=httpx.AsyncClient(transport=_transport(calls))
    )
    provider = HomeAssistantDeviceProvider(
        client,
        device_id_resolver=lambda state: (
            "physical-hub" if state["entity_id"] != "lock.front" else None
        ),
    )

    inventory = await provider.discover()

    physical = next(
        device for device in inventory.devices if device.external_ref == "device:physical-hub"
    )
    assert physical.metadata["virtual"] is False
    assert set(physical.metadata["entity_ids"]) == {"light.desk", "climate.living"}
    assert len(inventory.devices) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_sync_marks_a_disappeared_provider_binding_missing(tmp_path) -> None:
    calls: list[httpx.Request] = []
    client = HomeAssistantClient(
        "http://ha.test", "not-a-real-token", client=httpx.AsyncClient(transport=_transport(calls))
    )
    provider = HomeAssistantDeviceProvider(client)
    initial = await provider.discover()

    class InventorySequenceProvider:
        provider_id = provider.provider_id
        provider_type = provider.provider_type

        def __init__(self) -> None:
            self._inventories = [
                initial,
                ProviderInventory(provider_id="home_assistant", provider_type="home_assistant"),
            ]

        async def discover(self) -> ProviderInventory:
            return self._inventories.pop(0)

    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'iot.db'}")
    await initialize_database(engine)
    sessions = create_session_factory(engine)
    device_repo = DeviceRepository(sessions)
    service = DeviceSyncService(
        InventorySequenceProvider(),
        ThingModelRepository(sessions),
        device_repo,
        StateRepository(sessions),
    )

    await service.sync()
    result = await service.sync()

    assert result.missing == 3
    assert {device.status for device in await device_repo.list_devices()} == {"missing"}
    await client.aclose()
    await engine.dispose()

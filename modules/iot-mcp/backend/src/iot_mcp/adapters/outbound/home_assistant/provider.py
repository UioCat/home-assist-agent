"""Home Assistant DeviceProvider backed by REST and an injectable event source."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any

from iot_mcp.adapters.outbound.home_assistant.client import HomeAssistantClient, HomeAssistantError
from iot_mcp.adapters.outbound.home_assistant.mapping import (
    capability_fingerprint,
    map_ha_state,
    properties_from_state,
    service_for_properties,
)
from iot_mcp.ports.device_provider import (
    DeviceState,
    EventSink,
    ProviderEvent,
    ProviderHealth,
    ProviderInventory,
    ProviderResult,
    Subscription,
)

EventSource = Callable[[], AsyncIterator[dict[str, Any]]]
DeviceIdResolver = Callable[[dict[str, Any]], str | None]


class _TaskSubscription:
    def __init__(self, task: asyncio.Task[None]) -> None:
        self._task = task

    async def close(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


class HomeAssistantDeviceProvider:
    provider_id = "home_assistant"
    provider_type = "home_assistant"

    def __init__(
        self,
        client: HomeAssistantClient,
        *,
        event_source: EventSource | None = None,
        device_id_resolver: DeviceIdResolver | None = None,
    ) -> None:
        self._client = client
        self._event_source = event_source
        self._device_id_resolver = device_id_resolver
        self._routes: dict[str, dict[str, str]] = {}

    async def health(self) -> ProviderHealth:
        try:
            await self._client.get_states()
        except HomeAssistantError as error:
            return ProviderHealth(status=error.category, detail=str(error))
        return ProviderHealth(status="healthy")

    async def discover(self) -> ProviderInventory:
        states = await self._client.get_states()
        self._routes = {}
        devices: dict[str, Any] = {}
        for state in states:
            if str(state.get("entity_id", "")).partition(".")[0] not in {
                "light",
                "switch",
                "climate",
                "lock",
            }:
                continue
            device_id = (state.get("attributes") or {}).get("device_id")
            if self._device_id_resolver is not None:
                device_id = self._device_id_resolver(state)
            device = map_ha_state(state, device_id=device_id)
            routes = self._routes.setdefault(device.external_ref, {})
            for binding in device.feature_bindings:
                routes[binding["identifier"]] = binding["provider_selector"]["entity_id"]
            previous = devices.get(device.external_ref)
            if previous is None:
                devices[device.external_ref] = device
                continue
            merged = previous.model_copy(
                update={
                    "feature_bindings": previous.feature_bindings + device.feature_bindings,
                    "state": previous.state.model_copy(
                        update={"values": {**previous.state.values, **device.state.values}}
                    ),
                    "metadata": {
                        **previous.metadata,
                        "entity_ids": previous.metadata["entity_ids"]
                        + device.metadata["entity_ids"],
                    },
                }
            )
            fingerprint = capability_fingerprint(merged)
            devices[device.external_ref] = merged.model_copy(
                update={
                    "capability_fingerprint": fingerprint,
                    "product_key": f"ha-{fingerprint[7:23]}",
                }
            )
        return ProviderInventory(
            provider_id=self.provider_id,
            provider_type=self.provider_type,
            devices=list(devices.values()),
        )

    def _entity_id(self, device_ref: str, identifier: str | None = None) -> str:
        if device_ref in self._routes:
            routes = self._routes[device_ref]
            if identifier is not None and identifier in routes:
                return routes[identifier]
            return next(iter(routes.values()))
        if device_ref.startswith("entity:"):
            return device_ref.removeprefix("entity:")
        raise HomeAssistantError("target_not_found", f"no route for {device_ref}")

    async def read_state(self, device_ref: str, selectors: list[str] | None = None) -> DeviceState:
        if device_ref in self._routes:
            routes = self._routes[device_ref]
            entity_ids = (
                {routes[selector] for selector in selectors if selector in routes}
                if selectors
                else set(routes.values())
            )
        else:
            entity_ids = {self._entity_id(device_ref)}
        values: dict[str, Any] = {}
        for entity_id in entity_ids:
            values.update(properties_from_state(await self._client.get_state(entity_id)))
        if selectors is not None:
            values = {key: value for key, value in values.items() if key in selectors}
        return DeviceState(device_ref=device_ref, values=values)

    async def write_properties(self, device_ref: str, values: dict[str, Any]) -> ProviderResult:
        try:
            before = await self.read_state(device_ref)
            entity_id = self._entity_id(device_ref, next(iter(values)))
            domain, service, payload = service_for_properties(entity_id, values)
            response = await self._client.call_service(domain, service, payload)
            after = await self.read_state(device_ref)
        except HomeAssistantError as error:
            return ProviderResult(ok=False, error_code=error.category, message=str(error))
        except ValueError as error:
            return ProviderResult(ok=False, error_code="invalid_request", message=str(error))
        return ProviderResult(
            ok=True,
            data={"before": before.values, "after": after.values, "provider_response": response},
        )

    async def invoke_service(
        self, device_ref: str, service: str, inputs: dict[str, Any]
    ) -> ProviderResult:
        try:
            entity_id = self._entity_id(device_ref)
            domain, separator, service_name = service.partition(".")
            if not separator:
                domain, service_name = entity_id.partition(".")[0], service
            response = await self._client.call_service(
                domain, service_name, {"entity_id": entity_id, **inputs}
            )
            return ProviderResult(ok=True, data={"provider_response": response})
        except HomeAssistantError as error:
            return ProviderResult(ok=False, error_code=error.category, message=str(error))

    async def subscribe(self, sink: EventSink) -> Subscription:
        source = self._event_source or self._client.websocket_events

        async def consume() -> None:
            async for message in source():
                event = self._map_event(message)
                if event is None:
                    continue
                result = sink(event)
                if inspect.isawaitable(result):
                    await result

        return _TaskSubscription(asyncio.create_task(consume()))

    def _map_event(self, message: dict[str, Any]) -> ProviderEvent | None:
        data = message.get("event", {}).get("data", message.get("data", {}))
        entity_id = data.get("entity_id")
        if not isinstance(entity_id, str):
            return None
        ref = next(
            (key for key, value in self._routes.items() if value == entity_id),
            f"entity:{entity_id}",
        )
        return ProviderEvent(
            device_ref=ref, identifier="state_changed", values={"entity_id": entity_id}
        )

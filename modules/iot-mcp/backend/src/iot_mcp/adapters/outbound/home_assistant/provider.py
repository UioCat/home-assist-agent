"""Home Assistant DeviceProvider backed by REST and an injectable event source."""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any
from uuid import uuid4

from iot_mcp.adapters.outbound.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantError,
    HomeAssistantTimeout,
)
from iot_mcp.adapters.outbound.home_assistant.mapping import (
    availability_from_state,
    capability_fingerprint,
    map_ha_state,
    properties_from_state,
    service_for_properties,
)
from iot_mcp.ports.device_provider import (
    DeviceState,
    EventSink,
    ProviderDevice,
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

    async def wait(self) -> None:
        await self._task

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
        self._routes: dict[str, dict[str, dict[str, Any]]] = {}
        self._availability_by_ref: dict[str, dict[str, str]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self, *, message_id: str | None = None) -> ProviderHealth:
        operation_id = message_id or str(uuid4())
        try:
            await self._client.get_states(message_id=operation_id)
        except HomeAssistantError as error:
            return ProviderHealth(status=error.category, detail=str(error))
        return ProviderHealth(status="healthy")

    async def discover(self, *, message_id: str | None = None) -> ProviderInventory:
        operation_id = message_id or str(uuid4())
        entity_registry = await self._client.get_entity_registry(message_id=operation_id)
        device_registry = await self._client.get_device_registry(message_id=operation_id)
        area_registry = await self._client.get_area_registry(message_id=operation_id)
        states = await self._client.get_states(message_id=operation_id)
        services = await self._client.get_services(message_id=operation_id)
        config = await self._client.get_config(message_id=operation_id)

        entity_entries = {
            entry["entity_id"]: entry
            for entry in entity_registry
            if isinstance(entry.get("entity_id"), str)
        }
        devices_by_id = {
            str(entry["id"]): entry
            for entry in device_registry
            if entry.get("id") is not None
        }
        areas_by_id = {
            str(entry["area_id"]): entry
            for entry in area_registry
            if entry.get("area_id") is not None
        }
        services_by_domain = {
            str(item.get("domain")): (
                item.get("services")
                if isinstance(item.get("services"), dict)
                else {}
            )
            for item in services
            if item.get("domain") is not None
        }
        grouped: dict[str, list[ProviderDevice]] = defaultdict(list)
        registry_by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for state in states:
            entity_id = str(state.get("entity_id", ""))
            domain = entity_id.partition(".")[0]
            if domain not in {"light", "switch", "climate", "lock"}:
                continue
            registry_entry = entity_entries.get(entity_id, {})
            if registry_entry.get("entity_category") in {"config", "diagnostic"}:
                continue
            device_id = registry_entry.get("device_id")
            if self._device_id_resolver is not None:
                device_id = self._device_id_resolver(state)
            mapped = map_ha_state(
                state,
                device_id=str(device_id) if device_id else None,
                registry_entry=registry_entry,
                services=services_by_domain.get(domain, {}),
            )
            grouped[mapped.external_ref].append(mapped)
            registry_by_ref[mapped.external_ref].append(registry_entry)

        self._routes = {}
        self._availability_by_ref = {}
        discovered: list[ProviderDevice] = []
        for external_ref, entities in sorted(grouped.items()):
            device_id = (
                external_ref.removeprefix("device:")
                if external_ref.startswith("device:")
                else None
            )
            registry_device = devices_by_id.get(device_id or "", {})
            entity_entries_for_device = registry_by_ref[external_ref]
            area_id = registry_device.get("area_id") or next(
                (
                    entry.get("area_id")
                    for entry in entity_entries_for_device
                    if entry.get("area_id")
                ),
                None,
            )
            area_entry = areas_by_id.get(str(area_id), {}) if area_id else {}
            assembled = self._assemble_device(entities)
            display_name = (
                registry_device.get("name_by_user")
                or registry_device.get("name")
                or assembled.display_name
            )
            metadata = {
                **assembled.metadata,
                "entity_registry": [
                    _safe_registry_metadata(entry)
                    for entry in entity_entries_for_device
                ],
                "device_registry": _safe_registry_metadata(registry_device),
                "area": _safe_registry_metadata(area_entry),
                "ha_config": {
                    key: config[key]
                    for key in ("location_name", "version", "time_zone")
                    if key in config
                },
                "service_domains": sorted(
                    {
                        str(binding["provider_selector"].get("domain"))
                        for binding in assembled.feature_bindings
                        if binding["feature_type"] == "service"
                    }
                ),
            }
            device_type, device_type_label = _classify_device_type(
                registry_device,
                assembled,
            )
            metadata.update(
                {
                    "device_type": device_type,
                    "device_type_label": device_type_label,
                }
            )
            assembled = assembled.model_copy(
                update={
                    "display_name": str(display_name),
                    "area": (
                        str(area_entry.get("name"))
                        if area_entry.get("name")
                        else None
                    ),
                    "metadata": metadata,
                }
            )
            self._routes[external_ref] = {
                binding["identifier"]: dict(binding["provider_selector"])
                | {
                    "feature_type": binding["feature_type"],
                    "write_binding": binding.get("write_binding"),
                }
                for binding in assembled.feature_bindings
            }
            self._availability_by_ref[external_ref] = {
                entity_id: entity.state.availability
                for entity in entities
                for entity_id in entity.metadata.get("entity_ids", [])
            }
            discovered.append(assembled)
        return ProviderInventory(
            provider_id=self.provider_id,
            provider_type=self.provider_type,
            devices=discovered,
        )

    @staticmethod
    def _assemble_device(entities: list[ProviderDevice]) -> ProviderDevice:
        raw_bindings = [
            binding
            for entity in entities
            for binding in entity.feature_bindings
        ]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for binding in raw_bindings:
            selector = binding.get("provider_selector") or {}
            capability = str(selector.get("capability") or binding["identifier"])
            groups[(binding["feature_type"], capability)].append(binding)

        bindings: list[dict[str, Any]] = []
        values: dict[str, Any] = {}
        entity_values = {
            (
                str(binding["provider_selector"]["entity_id"]),
                str(binding["provider_selector"].get("capability") or binding["identifier"]),
            ): entity.state.values.get(
                str(binding["provider_selector"].get("capability") or binding["identifier"])
            )
            for entity in entities
            for binding in entity.feature_bindings
            if binding["feature_type"] == "property"
        }
        for (feature_type, capability), candidates in sorted(groups.items()):
            ordered = sorted(
                candidates,
                key=lambda binding: (
                    str(binding["provider_selector"].get("registry_identity", "")),
                    str(binding["provider_selector"].get("entity_id", "")),
                ),
            )
            for index, binding in enumerate(ordered, start=1):
                identifier = (
                    capability
                    if len(ordered) == 1
                    else f"{capability}_{index}"
                )
                normalized = {**binding, "identifier": identifier}
                bindings.append(normalized)
                if feature_type == "property":
                    selector = normalized["provider_selector"]
                    value = entity_values.get(
                        (str(selector["entity_id"]), capability)
                    )
                    if value is not None:
                        values[identifier] = value

        seed = entities[0]
        domains = sorted(
            {
                str(binding["provider_selector"].get("domain", ""))
                for binding in bindings
            }
        )
        assembled = seed.model_copy(
            update={
                "feature_bindings": bindings,
                "state": seed.state.model_copy(
                    update={
                        "values": values,
                        "availability": _aggregate_availability(entities),
                    }
                ),
                "metadata": {
                    "entity_ids": sorted(
                        {
                            str(binding["provider_selector"]["entity_id"])
                            for binding in bindings
                        }
                    ),
                    "registry_identities": sorted(
                        {
                            str(binding["provider_selector"]["registry_identity"])
                            for binding in bindings
                        }
                    ),
                    "virtual": seed.external_ref.startswith("entity:"),
                    "domains": domains,
                },
                "product_name": f"Home Assistant {' + '.join(domains)}",
                "risk_level": (
                    "high"
                    if any(entity.risk_level == "high" for entity in entities)
                    else "low"
                ),
            }
        )
        fingerprint = capability_fingerprint(assembled)
        return assembled.model_copy(
            update={
                "capability_fingerprint": fingerprint,
                "product_key": f"ha-{fingerprint[7:23]}",
            }
        )

    def _selector(
        self,
        device_ref: str,
        identifier: str,
        *,
        feature_type: str,
    ) -> dict[str, Any]:
        route = self._routes.get(device_ref, {}).get(identifier)
        if route is None or route.get("feature_type") != feature_type:
            raise HomeAssistantError(
                "target_not_found",
                f"unknown {feature_type} identifier {identifier!r} for {device_ref}",
            )
        return route

    async def read_state(
        self,
        device_ref: str,
        selectors: list[str] | None = None,
        *,
        message_id: str | None = None,
    ) -> DeviceState:
        operation_id = message_id or str(uuid4())
        routes = self._routes.get(device_ref)
        if routes is None:
            raise HomeAssistantError(
                "target_not_found", f"no route for {device_ref}"
            )
        identifiers = (
            selectors
            if selectors is not None
            else [
                identifier
                for identifier, route in routes.items()
                if route.get("feature_type") == "property"
            ]
        )
        selected_routes = {
            identifier: self._selector(
                device_ref,
                identifier,
                feature_type="property",
            )
            for identifier in identifiers
        }
        entity_ids = {
            str(route["entity_id"]) for route in selected_routes.values()
        }
        states_by_entity = {
            entity_id: await self._client.get_state(
                entity_id, message_id=operation_id
            )
            for entity_id in entity_ids
        }
        raw_by_entity = {
            entity_id: properties_from_state(state)
            for entity_id, state in states_by_entity.items()
        }
        availability_by_entity = self._availability_by_ref.setdefault(
            device_ref, {}
        )
        availability_by_entity.update(
            {
                entity_id: availability_from_state(state)
                for entity_id, state in states_by_entity.items()
            }
        )
        values = {
            identifier: raw_by_entity[str(route["entity_id"])][
                str(route.get("capability") or identifier)
            ]
            for identifier, route in selected_routes.items()
            if str(route.get("capability") or identifier)
            in raw_by_entity[str(route["entity_id"])]
        }
        return DeviceState(
            device_ref=device_ref,
            values=values,
            availability=_aggregate_availability_values(
                availability_by_entity.values()
            ),
        )

    async def write_properties(
        self,
        device_ref: str,
        values: dict[str, Any],
        *,
        message_id: str | None = None,
    ) -> ProviderResult:
        operation_id = message_id or str(uuid4())
        service_called = False
        try:
            before = await self.read_state(
                device_ref, list(values), message_id=operation_id
            )
            routes = {
                identifier: self._selector(
                    device_ref,
                    identifier,
                    feature_type="property",
                )
                for identifier in values
            }
            if any(route.get("write_binding") is None for route in routes.values()):
                raise ValueError("property is read-only")
            entity_ids = {
                str(route["entity_id"]) for route in routes.values()
            }
            if len(entity_ids) != 1:
                raise ValueError(
                    "properties from multiple HA entities must be written separately"
                )
            entity_id = entity_ids.pop()
            provider_values = {
                str(route.get("capability") or identifier): values[identifier]
                for identifier, route in routes.items()
            }
            domain, service, payload = service_for_properties(
                entity_id, provider_values
            )
            response = await self._client.call_service(
                domain, service, payload, message_id=operation_id
            )
            service_called = True
            after = await self.read_state(
                device_ref, list(values), message_id=operation_id
            )
        except HomeAssistantTimeout:
            raise
        except HomeAssistantError as error:
            if service_called:
                raise HomeAssistantTimeout(
                    "HA post-call verification outcome is unknown"
                ) from error
            return ProviderResult(
                ok=False,
                error_code=error.category,
                message=str(error),
            )
        except ValueError as error:
            return ProviderResult(
                ok=False,
                error_code="invalid_request",
                message=str(error),
            )
        return ProviderResult(
            ok=True,
            data={
                "before": before.values,
                "after": after.values,
                "provider_response": response,
            },
        )

    async def invoke_service(
        self,
        device_ref: str,
        service: str,
        inputs: dict[str, Any],
        *,
        message_id: str | None = None,
    ) -> ProviderResult:
        operation_id = message_id or str(uuid4())
        try:
            route = self._selector(
                device_ref, service, feature_type="service"
            )
            response = await self._client.call_service(
                str(route["domain"]),
                str(route["service"]),
                {"entity_id": route["entity_id"], **inputs},
                message_id=operation_id,
            )
            return ProviderResult(
                ok=True, data={"provider_response": response}
            )
        except HomeAssistantTimeout:
            raise
        except HomeAssistantError as error:
            return ProviderResult(
                ok=False,
                error_code=error.category,
                message=str(error),
            )

    async def subscribe(
        self, sink: EventSink, *, message_id: str | None = None
    ) -> Subscription:
        operation_id = message_id or str(uuid4())

        async def consume() -> None:
            source = (
                self._event_source()
                if self._event_source is not None
                else self._client.websocket_events(message_id=operation_id)
            )
            async for message in source:
                event = self._map_event(message)
                if event is None:
                    continue
                result = sink(event)
                if inspect.isawaitable(result):
                    await result

        return _TaskSubscription(asyncio.create_task(consume()))

    def _map_event(
        self, message: dict[str, Any]
    ) -> ProviderEvent | None:
        data = message.get("event", {}).get(
            "data", message.get("data", {})
        )
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        if not isinstance(entity_id, str) or not isinstance(new_state, dict):
            return None
        ref = next(
            (
                device_ref
                for device_ref, routes in self._routes.items()
                if entity_id
                in {
                    str(route.get("entity_id"))
                    for route in routes.values()
                }
            ),
            None,
        )
        raw = properties_from_state(new_state)
        if ref is None:
            if self._routes:
                return None
            return ProviderEvent(
                device_ref=f"entity:{entity_id}",
                identifier="state_changed",
                values=raw,
                availability=availability_from_state(new_state),
            )
        availability_by_entity = self._availability_by_ref.setdefault(ref, {})
        availability_by_entity[entity_id] = availability_from_state(new_state)
        values = {
            identifier: raw[capability]
            for identifier, route in self._routes[ref].items()
            if route.get("feature_type") == "property"
            and route.get("entity_id") == entity_id
            and (capability := str(route.get("capability") or identifier))
            in raw
        }
        return ProviderEvent(
            device_ref=ref,
            identifier="state_changed",
            values=values,
            availability=_aggregate_availability_values(
                availability_by_entity.values()
            ),
        )


_DEVICE_TYPE_LABELS = {
    "light": "灯具",
    "outlet": "插座",
    "climate": "温控",
    "heater": "取暖器",
    "humidifier": "加湿器",
    "lock": "门锁",
    "appliance": "家电",
    "switch": "开关",
    "other": "其他",
}


def _aggregate_availability(entities: list[ProviderDevice]) -> str:
    return _aggregate_availability_values(
        entity.state.availability for entity in entities
    )


def _aggregate_availability_values(values: Iterable[str]) -> str:
    availability = set(values)
    if "online" in availability:
        return "online"
    if "unknown" in availability:
        return "unknown"
    return "offline"


def _classify_device_type(
    registry_device: dict[str, Any], assembled: ProviderDevice
) -> tuple[str, str]:
    haystack = " ".join(
        str(registry_device.get(key) or "")
        for key in ("model", "name", "name_by_user")
    ).casefold()
    keyword_types = (
        ("outlet", ("plug", "outlet", "插座", "排插")),
        ("heater", ("heater", "radiator", "取暖", "电暖", "暖风")),
        ("humidifier", ("humidifier", "加湿")),
        ("appliance", ("cooker", "oven", "kettle", "电磁炉", "烤箱", "热水壶")),
        ("lock", ("lock", "门锁", "智能锁")),
        ("light", ("lamp", "light", "灯具", "挂灯", "台灯", "灯带")),
    )
    for device_type, keywords in keyword_types:
        if any(keyword in haystack for keyword in keywords):
            return device_type, _DEVICE_TYPE_LABELS[device_type]

    domains = set(assembled.metadata.get("domains") or ())
    for device_type in ("lock", "climate", "light", "switch"):
        if device_type in domains:
            return device_type, _DEVICE_TYPE_LABELS[device_type]
    return "other", _DEVICE_TYPE_LABELS["other"]


def _safe_registry_metadata(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "area_id",
        "config_entries",
        "device_id",
        "disabled_by",
        "entity_category",
        "entity_id",
        "id",
        "manufacturer",
        "model",
        "name",
        "name_by_user",
        "platform",
        "unique_id",
    }
    return {
        key: value[key]
        for key in sorted(allowed)
        if key in value
    }

"""Deterministic in-memory provider for contract and local integration tests."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any

from iot_mcp.domain.models import utc_now
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


class _MockSubscription:
    def __init__(self, provider: MockDeviceProvider, sink: EventSink) -> None:
        self._provider = provider
        self._sink = sink

    async def close(self) -> None:
        self._provider._sinks.remove(self._sink)


class MockDeviceProvider:
    provider_id = "mock"
    provider_type = "mock"

    def __init__(self, *, fail_operations: Iterable[str] = ()) -> None:
        self._fail_operations = set(fail_operations)
        self._sinks: list[EventSink] = []
        self._states: dict[str, dict[str, Any]] = {
            "mock:light:desk": {"PowerSwitch": True, "Brightness": 50},
            "mock:climate:living_room": {
                "PowerSwitch": True,
                "CurrentTemperature": 20.0,
                "TargetTemperature": 22.0,
            },
            "mock:lock:front_door": {"LockState": "LOCK"},
        }

    async def health(self) -> ProviderHealth:
        return ProviderHealth(status="healthy")

    async def discover(self) -> ProviderInventory:
        devices = [
            self._device("mock:light:desk", "Desk light", "mock-light", "Mock dimmable light"),
            self._device(
                "mock:climate:living_room", "Living room AC", "mock-climate", "Mock climate"
            ),
            self._device(
                "mock:lock:front_door", "Front door", "mock-lock", "Mock lock", risk_level="high"
            ),
        ]
        return ProviderInventory(
            provider_id=self.provider_id, provider_type=self.provider_type, devices=devices
        )

    def _device(
        self, ref: str, name: str, fingerprint: str, product_name: str, *, risk_level: str = "low"
    ) -> ProviderDevice:
        return ProviderDevice(
            external_ref=ref,
            display_name=name,
            capability_fingerprint=fingerprint,
            product_key=fingerprint,
            product_name=product_name,
            state=DeviceState(device_ref=ref, values=dict(self._states[ref])),
            metadata={"mock": True},
            risk_level=risk_level,
        )

    async def read_state(self, device_ref: str, selectors: list[str] | None = None) -> DeviceState:
        if device_ref not in self._states:
            return DeviceState(device_ref=device_ref, values={}, freshness="unknown")
        values = self._states[device_ref]
        if selectors is not None:
            values = {key: value for key, value in values.items() if key in selectors}
        return DeviceState(device_ref=device_ref, values=dict(values))

    async def write_properties(self, device_ref: str, values: dict[str, Any]) -> ProviderResult:
        if "write_properties" in self._fail_operations:
            return ProviderResult(
                ok=False, error_code="injected_failure", message="write_properties"
            )
        if device_ref not in self._states:
            return ProviderResult(ok=False, error_code="target_not_found")
        self._states[device_ref].update(values)
        await self._emit(device_ref, "state_changed", dict(values))
        return ProviderResult(ok=True, data={"after": dict(self._states[device_ref])})

    async def invoke_service(
        self, device_ref: str, service: str, inputs: dict[str, Any]
    ) -> ProviderResult:
        if "invoke_service" in self._fail_operations:
            return ProviderResult(ok=False, error_code="injected_failure", message="invoke_service")
        return await self.write_properties(device_ref, {"service": service, **inputs})

    async def subscribe(self, sink: EventSink) -> Subscription:
        self._sinks.append(sink)
        return _MockSubscription(self, sink)

    async def _emit(self, ref: str, identifier: str, values: dict[str, Any]) -> None:
        event = ProviderEvent(
            device_ref=ref, identifier=identifier, values=values, occurred_at=utc_now()
        )
        for sink in list(self._sinks):
            result = sink(event)
            if inspect.isawaitable(result):
                await result

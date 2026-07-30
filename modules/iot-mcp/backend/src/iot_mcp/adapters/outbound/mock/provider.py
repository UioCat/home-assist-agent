"""Deterministic in-memory provider for contract and local integration tests."""

from __future__ import annotations

import asyncio
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
        self._closed = asyncio.Event()

    async def wait(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        if self._sink in self._provider._sinks:
            self._provider._sinks.remove(self._sink)
        self._closed.set()


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
            feature_bindings=_mock_bindings(ref),
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
        if device_ref not in self._states:
            return ProviderResult(
                ok=False,
                error_code="target_not_found",
                message="mock device was not found",
            )
        before = dict(self._states[device_ref])
        if device_ref == "mock:light:desk" and service == "Toggle":
            self._states[device_ref]["PowerSwitch"] = not bool(
                self._states[device_ref]["PowerSwitch"]
            )
        elif (
            device_ref == "mock:climate:living_room"
            and service == "SetTemperature"
            and isinstance(inputs.get("temperature"), int | float)
        ):
            self._states[device_ref]["TargetTemperature"] = inputs[
                "temperature"
            ]
        elif device_ref == "mock:lock:front_door" and service in {
            "Lock",
            "Unlock",
        }:
            self._states[device_ref]["LockState"] = (
                "LOCK" if service == "Lock" else "UNLOCK"
            )
        else:
            return ProviderResult(
                ok=False,
                error_code="target_not_found",
                message="mock service was not found",
            )
        after = dict(self._states[device_ref])
        await self._emit(device_ref, "state_changed", after)
        return ProviderResult(ok=True, data={"before": before, "after": after})

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


def _mock_bindings(ref: str) -> list[dict[str, Any]]:
    if ref == "mock:light:desk":
        properties = [
            ("PowerSwitch", {"type": "bool", "specs": {}}, "rw"),
            (
                "Brightness",
                {"type": "int", "specs": {"min": 0, "max": 100}},
                "rw",
            ),
        ]
        services = [("Toggle", [])]
    elif ref == "mock:climate:living_room":
        properties = [
            ("PowerSwitch", {"type": "bool", "specs": {}}, "rw"),
            (
                "CurrentTemperature",
                {"type": "double", "specs": {}},
                "r",
            ),
            (
                "TargetTemperature",
                {"type": "double", "specs": {"min": 5, "max": 35}},
                "rw",
            ),
        ]
        services = [
            (
                "SetTemperature",
                [
                    {
                        "identifier": "temperature",
                        "name": "Temperature",
                        "required": True,
                        "dataType": {
                            "type": "double",
                            "specs": {"min": 5, "max": 35},
                        },
                    }
                ],
            )
        ]
    else:
        properties = [
            (
                "LockState",
                {
                    "type": "enum",
                    "specs": {
                        "LOCK": "Locked",
                        "UNLOCK": "Unlocked",
                    },
                },
                "rw",
            )
        ]
        services = [("Lock", []), ("Unlock", [])]
    bindings = [
        {
            "feature_type": "property",
            "identifier": identifier,
            "provider_selector": {
                "capability": identifier,
                "device_ref": ref,
            },
            "read_binding": {
                "source": "state",
                "name": identifier,
                "access_mode": access_mode,
                "data_type": data_type,
            },
            "write_binding": (
                {"operation": "write_properties"}
                if access_mode == "rw"
                else None
            ),
            "transformer": None,
            "risk_level": (
                "high" if identifier == "LockState" else None
            ),
        }
        for identifier, data_type, access_mode in properties
    ]
    bindings.extend(
        {
            "feature_type": "service",
            "identifier": identifier,
            "provider_selector": {
                "capability": identifier,
                "device_ref": ref,
                "service": identifier,
            },
            "read_binding": None,
            "write_binding": {
                "name": identifier,
                "call_type": "async",
                "input_data": inputs,
                "output_data": [],
            },
            "transformer": None,
            "risk_level": (
                "high"
                if ref == "mock:lock:front_door"
                else None
            ),
        }
        for identifier, inputs in services
    )
    return bindings

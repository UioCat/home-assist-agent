from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.application.policy import SafeControlError
from iot_mcp.bootstrap.runtime import build_runtime
from iot_mcp.config.settings import Settings
from iot_mcp.ports.device_provider import ProviderEvent


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record(self, **event: Any) -> None:
        self.events.append(event)


async def test_provider_event_persistence_has_one_complete_message_chain(
    tmp_path: Path,
) -> None:
    audit = RecordingAudit()
    provider = MockDeviceProvider()
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'events.db'}",
            audit_database_path=str(tmp_path / "audit.db"),
        ),
        providers={"mock": provider},
        audit=audit,
    )
    await runtime.startup()
    try:
        audit.events.clear()
        event = ProviderEvent(
            message_id="ha-event-123",
            device_ref="mock:light:desk",
            identifier="state_changed",
            values={"PowerState": "ON"},
        )

        await runtime.container._persist_provider_event("mock", provider, event)

        assert [item["event_type"] for item in audit.events] == [
            "system.request",
            "system.response",
        ]
        assert {item["message_id"] for item in audit.events} == {"ha-event-123"}
        assert audit.events[-1]["payload"]["persisted"] is True
    finally:
        await runtime.shutdown()


async def test_provider_event_updates_device_availability_projection(tmp_path: Path) -> None:
    audit = RecordingAudit()
    provider = MockDeviceProvider()
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'availability.db'}",
            audit_database_path=str(tmp_path / "availability-audit.db"),
        ),
        providers={"mock": provider},
        audit=audit,
    )
    await runtime.startup()
    try:
        light = next(
            device
            for device in await runtime.container.devices.list_devices()
            if device.display_name == "Desk light"
        )

        await runtime.container._persist_provider_event(
            "mock",
            provider,
            ProviderEvent(
                message_id="availability-event",
                device_ref="mock:light:desk",
                identifier="state_changed",
                values={"PowerSwitch": False},
                availability="offline",
            ),
        )

        updated = await runtime.container.devices.get_device(light.device_id)
        cards = await runtime.container.queries.list_device_cards()
        card = next(item for item in cards if item["device_id"] == light.device_id)
        assert updated is not None and updated.status == "offline"
        assert card["availability"] == "offline"
        assert (card["device_type"], card["device_type_label"]) == ("light", "灯具")
        assert card["primary_control"] is None
    finally:
        await runtime.shutdown()


async def test_missing_device_is_hidden_from_current_queries(tmp_path: Path) -> None:
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}",
            audit_database_path=str(tmp_path / "missing-audit.db"),
        ),
        providers={"mock": MockDeviceProvider()},
    )
    await runtime.startup()
    try:
        light = next(
            device
            for device in await runtime.container.devices.list_devices()
            if device.display_name == "Desk light"
        )
        await runtime.container.devices.upsert_device(
            light.model_copy(update={"status": "missing"})
        )

        assert light.device_id not in {
            device.device_id for device in await runtime.container.queries.list_devices()
        }
        assert light.device_id not in {
            card["device_id"] for card in await runtime.container.queries.list_device_cards()
        }
        with pytest.raises(SafeControlError) as raised:
            await runtime.container.queries.get_device(light.device_id)
        assert getattr(raised.value, "code", None) == "target_not_found"
    finally:
        await runtime.shutdown()


async def test_unmapped_provider_event_is_ignored_without_degrading_provider(
    tmp_path: Path,
) -> None:
    audit = RecordingAudit()
    provider = MockDeviceProvider()
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'ignored.db'}",
            audit_database_path=str(tmp_path / "ignored-audit.db"),
        ),
        providers={"mock": provider},
        audit=audit,
    )
    await runtime.startup()
    try:
        audit.events.clear()
        await runtime.container._persist_provider_event(
            "mock",
            provider,
            ProviderEvent(
                message_id="ignored-event",
                device_ref="entity:sensor.unsupported",
                identifier="state_changed",
                values={"Value": 1},
            ),
        )

        assert runtime.container.provider_status["mock"] == "healthy"
        assert audit.events[-1].get("status", "success") == "success"
        assert audit.events[-1]["payload"] == {
            "persisted": False,
            "ignored": True,
            "reason": "binding_not_found",
        }
    finally:
        await runtime.shutdown()

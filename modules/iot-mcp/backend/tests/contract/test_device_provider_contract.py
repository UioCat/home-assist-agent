import pytest

from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.ports.device_provider import ProviderEvent


@pytest.mark.asyncio
async def test_mock_provider_exercises_common_device_provider_contract() -> None:
    provider = MockDeviceProvider()
    health = await provider.health()
    inventory = await provider.discover()

    assert health.status == "healthy"
    assert {device.external_ref for device in inventory.devices} == {
        "mock:light:desk",
        "mock:climate:living_room",
        "mock:lock:front_door",
    }
    light = next(device for device in inventory.devices if device.external_ref == "mock:light:desk")
    climate = next(
        device
        for device in inventory.devices
        if device.external_ref == "mock:climate:living_room"
    )
    assert any(
        binding["feature_type"] == "service"
        and binding["identifier"] == "SetTemperature"
        for binding in climate.feature_bindings
    )
    state = await provider.read_state(light.external_ref, ["PowerSwitch", "Brightness"])
    assert state.values["Brightness"] == 50

    events: list[ProviderEvent] = []
    subscription = await provider.subscribe(events.append)
    result = await provider.write_properties(
        light.external_ref, {"PowerSwitch": True, "Brightness": 100}
    )
    updated = await provider.read_state(light.external_ref, ["Brightness"])

    assert result.ok
    assert updated.values["Brightness"] == 100
    assert events[-1].identifier == "state_changed"
    service_result = await provider.invoke_service(
        climate.external_ref,
        "SetTemperature",
        {"temperature": 24},
    )
    assert service_result.ok
    assert (
        await provider.read_state(climate.external_ref, ["TargetTemperature"])
    ).values == {"TargetTemperature": 24}
    await subscription.close()


@pytest.mark.asyncio
async def test_mock_provider_fault_injection_is_deterministic() -> None:
    provider = MockDeviceProvider(fail_operations={"write_properties"})

    result = await provider.write_properties("mock:light:desk", {"PowerSwitch": False})

    assert not result.ok
    assert result.error_code == "injected_failure"

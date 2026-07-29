from iot_mcp.adapters.outbound.home_assistant.mapping import (
    capability_fingerprint,
    ha_brightness_to_percent,
    map_ha_state,
    percent_to_ha_brightness,
    service_for_properties,
)


def test_brightness_conversion_preserves_boundaries_and_round_trips() -> None:
    assert ha_brightness_to_percent(0) == 0
    assert ha_brightness_to_percent(255) == 100
    assert percent_to_ha_brightness(0) == 0
    assert percent_to_ha_brightness(100) == 255
    assert ha_brightness_to_percent(percent_to_ha_brightness(50)) == 50


def test_capability_fingerprint_excludes_display_name_and_entity_id() -> None:
    first = map_ha_state(
        {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}}
    )
    second = map_ha_state(
        {"entity_id": "light.renamed", "state": "off", "attributes": {"friendly_name": "Desk"}}
    )

    assert capability_fingerprint(first) == capability_fingerprint(second)
    assert first.product_key == second.product_key


def test_mapping_creates_stable_virtual_device_for_entity_without_device_id() -> None:
    mapped = map_ha_state(
        {
            "entity_id": "switch.guest_outlet",
            "state": "off",
            "attributes": {"friendly_name": "Guest outlet"},
        }
    )

    assert mapped.external_ref == "entity:switch.guest_outlet"
    assert mapped.metadata["virtual"] is True
    assert mapped.state.values["PowerSwitch"] is False


def test_light_service_combines_power_and_brightness_without_state_writes() -> None:
    domain, service, payload = service_for_properties(
        "light.desk", {"PowerSwitch": True, "Brightness": 100}
    )

    assert (domain, service) == ("light", "turn_on")
    assert payload == {"entity_id": "light.desk", "brightness": 255}

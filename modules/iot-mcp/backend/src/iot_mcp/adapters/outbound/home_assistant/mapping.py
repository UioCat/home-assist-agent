"""Pure Home Assistant to normalized-device mapping rules."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from iot_mcp.ports.device_provider import DeviceState, ProviderDevice


def ha_brightness_to_percent(value: int | float) -> int:
    """Convert HA's inclusive 0-255 brightness to the TSL 0-100 range."""
    return round(max(0, min(255, float(value))) * 100 / 255)


def percent_to_ha_brightness(value: int | float) -> int:
    """Convert the TSL 0-100 brightness range to HA's inclusive 0-255 range."""
    return round(max(0, min(100, float(value))) * 255 / 100)


def _domain(state: dict[str, Any]) -> str:
    return str(state["entity_id"]).partition(".")[0]


def _features_for_domain(domain: str) -> list[str]:
    return {
        "light": ["PowerSwitch", "Brightness"],
        "switch": ["PowerSwitch"],
        "climate": ["PowerSwitch", "CurrentTemperature", "TargetTemperature"],
        "lock": ["LockState"],
    }.get(domain, [])


def capability_fingerprint(device: ProviderDevice) -> str:
    """Hash only stable semantic capabilities, never names or entity routing ids."""
    canonical = "|".join(sorted(binding["identifier"] for binding in device.feature_bindings))
    return f"sha256:{sha256(canonical.encode()).hexdigest()}"


def _bindings(domain: str, entity_id: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for identifier in _features_for_domain(domain):
        writable = identifier in {"PowerSwitch", "Brightness", "TargetTemperature", "LockState"}
        bindings.append(
            {
                "feature_type": "property",
                "identifier": identifier,
                "provider_selector": {"entity_id": entity_id},
                "read_binding": {"source": "state"},
                "write_binding": {"service": True} if writable else None,
                "transformer": {"kind": "brightness_percent"}
                if identifier == "Brightness"
                else None,
            }
        )
    return bindings


def properties_from_state(state: dict[str, Any]) -> dict[str, Any]:
    domain = _domain(state)
    attributes = state.get("attributes") or {}
    raw_state = state.get("state")
    if domain in {"light", "switch"}:
        values: dict[str, Any] = {"PowerSwitch": raw_state == "on"}
        if domain == "light" and "brightness" in attributes:
            values["Brightness"] = ha_brightness_to_percent(attributes["brightness"])
        return values
    if domain == "climate":
        values = {"PowerSwitch": raw_state not in {"off", "unavailable", "unknown"}}
        if "current_temperature" in attributes:
            values["CurrentTemperature"] = attributes["current_temperature"]
        if "temperature" in attributes:
            values["TargetTemperature"] = attributes["temperature"]
        return values
    if domain == "lock":
        return {"LockState": "LOCK" if raw_state == "locked" else "UNLOCK"}
    return {"State": raw_state}


def map_ha_state(state: dict[str, Any], *, device_id: str | None = None) -> ProviderDevice:
    entity_id = str(state["entity_id"])
    domain = _domain(state)
    attributes = state.get("attributes") or {}
    stable_ref = f"device:{device_id}" if device_id else f"entity:{entity_id}"
    bindings = _bindings(domain, entity_id)
    draft = ProviderDevice(
        external_ref=stable_ref,
        display_name=str(attributes.get("friendly_name", entity_id)),
        capability_fingerprint="",
        product_key="",
        product_name=f"Home Assistant {domain}",
        state=DeviceState(device_ref=stable_ref, values=properties_from_state(state)),
        feature_bindings=bindings,
        metadata={"entity_ids": [entity_id], "virtual": device_id is None, "domain": domain},
        risk_level="high" if domain == "lock" else "low",
    )
    fingerprint = capability_fingerprint(draft)
    return draft.model_copy(
        update={"capability_fingerprint": fingerprint, "product_key": f"ha-{fingerprint[7:23]}"}
    )


def service_for_properties(
    entity_id: str, values: dict[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    domain = entity_id.partition(".")[0]
    payload: dict[str, Any] = {"entity_id": entity_id}
    if domain == "light":
        if values.get("PowerSwitch") is False:
            return "light", "turn_off", payload
        if "Brightness" in values:
            payload["brightness"] = percent_to_ha_brightness(values["Brightness"])
        if values.get("PowerSwitch") is True or "Brightness" in values:
            return "light", "turn_on", payload
    if domain == "switch" and "PowerSwitch" in values:
        return "switch", "turn_on" if values["PowerSwitch"] else "turn_off", payload
    if domain == "climate" and "TargetTemperature" in values:
        payload["temperature"] = values["TargetTemperature"]
        return "climate", "set_temperature", payload
    if domain == "lock" and "LockState" in values:
        return "lock", "lock" if values["LockState"] == "LOCK" else "unlock", payload
    raise ValueError(f"unsupported properties for {entity_id}: {sorted(values)}")

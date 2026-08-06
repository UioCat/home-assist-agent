"""Pure Home Assistant to normalized-device mapping rules."""

from __future__ import annotations

import json
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


def _semantic_metadata(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        key: _semantic_metadata(child)
        for key, child in value.items()
        if key not in {"entity_id", "registry_identity", "name"}
    }


def capability_fingerprint(device: ProviderDevice) -> str:
    """Hash semantic capability multiplicity, never names or mutable route ids."""
    capabilities = []
    for binding in device.feature_bindings:
        selector = binding.get("provider_selector") or {}
        capabilities.append(
            {
                "feature_type": binding["feature_type"],
                "capability": selector.get("capability") or binding["identifier"],
                "read": _semantic_metadata(binding.get("read_binding")),
                "write": _semantic_metadata(binding.get("write_binding")),
            }
        )
    canonical = json.dumps(
        sorted(
            capabilities,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{sha256(canonical.encode()).hexdigest()}"


def property_data_type(identifier: str) -> dict[str, Any]:
    if identifier == "PowerSwitch":
        return {"type": "bool", "specs": {}}
    if identifier == "Brightness":
        return {"type": "int", "specs": {"min": 0, "max": 100}}
    if identifier in {"CurrentTemperature", "TargetTemperature"}:
        return {"type": "double", "specs": {}}
    if identifier == "LockState":
        return {
            "type": "enum",
            "specs": {"LOCK": "Locked", "UNLOCK": "Unlocked"},
        }
    return {"type": "text", "specs": {"length": 4096}}


def property_bindings(
    domain: str,
    entity_id: str,
    registry_identity: str,
) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for identifier in _features_for_domain(domain):
        writable = identifier in {
            "PowerSwitch",
            "Brightness",
            "TargetTemperature",
            "LockState",
        }
        bindings.append(
            {
                "feature_type": "property",
                "identifier": identifier,
                "provider_selector": {
                    "entity_id": entity_id,
                    "registry_identity": registry_identity,
                    "domain": domain,
                    "capability": identifier,
                },
                "read_binding": {
                    "source": "state",
                    "name": identifier,
                    "access_mode": "rw" if writable else "r",
                    "data_type": property_data_type(identifier),
                },
                "write_binding": {"service": True} if writable else None,
                "transformer": (
                    {"kind": "brightness_percent"}
                    if identifier == "Brightness"
                    else None
                ),
                "risk_level": "high" if identifier == "LockState" else None,
            }
        )
    return bindings


def _service_identifier(service: str) -> str:
    return "".join(part.capitalize() for part in service.split("_"))


def _service_input_data(fields: Any) -> list[dict[str, Any]]:
    if not isinstance(fields, dict):
        return []
    inputs: list[dict[str, Any]] = []
    for identifier, raw in sorted(fields.items()):
        field = raw if isinstance(raw, dict) else {}
        selector = field.get("selector")
        selector = selector if isinstance(selector, dict) else {}
        if isinstance(selector.get("number"), dict):
            number = selector["number"]
            data_type = {
                "type": "double",
                "specs": {
                    key: number[key]
                    for key in ("min", "max", "step")
                    if key in number
                },
            }
        elif "boolean" in selector:
            data_type = {"type": "bool", "specs": {}}
        elif isinstance(selector.get("select"), dict):
            options = selector["select"].get("options") or []
            specs = {
                str(option.get("value")): str(
                    option.get("label") or option.get("value")
                )
                for option in options
                if isinstance(option, dict) and option.get("value") is not None
            }
            data_type = (
                {"type": "enum", "specs": specs}
                if specs
                else {"type": "text", "specs": {"length": 4096}}
            )
        else:
            data_type = {"type": "text", "specs": {"length": 4096}}
        inputs.append(
            {
                "identifier": str(identifier),
                "name": str(field.get("name") or identifier),
                "required": bool(field.get("required", False)),
                "dataType": data_type,
            }
        )
    return inputs


def service_bindings(
    domain: str,
    entity_id: str,
    registry_identity: str,
    services: dict[str, Any],
) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for service, raw_definition in sorted(services.items()):
        definition = raw_definition if isinstance(raw_definition, dict) else {}
        identifier = _service_identifier(service)
        bindings.append(
            {
                "feature_type": "service",
                "identifier": identifier,
                "provider_selector": {
                    "entity_id": entity_id,
                    "registry_identity": registry_identity,
                    "domain": domain,
                    "service": service,
                    "capability": identifier,
                },
                "read_binding": None,
                "write_binding": {
                    "name": str(definition.get("name") or identifier),
                    "call_type": "async",
                    "input_data": _service_input_data(definition.get("fields")),
                    "output_data": [],
                },
                "transformer": None,
                "risk_level": "high" if domain == "lock" else None,
            }
        )
    return bindings


def properties_from_state(state: dict[str, Any]) -> dict[str, Any]:
    domain = _domain(state)
    attributes = state.get("attributes") or {}
    raw_state = state.get("state")
    if domain in {"light", "switch"}:
        values: dict[str, Any] = {"PowerSwitch": raw_state == "on"}
        brightness = attributes.get("brightness")
        if (
            domain == "light"
            and isinstance(brightness, int | float)
            and not isinstance(brightness, bool)
        ):
            values["Brightness"] = ha_brightness_to_percent(brightness)
        return values
    if domain == "climate":
        values = {
            "PowerSwitch": raw_state not in {"off", "unavailable", "unknown"}
        }
        if "current_temperature" in attributes:
            values["CurrentTemperature"] = attributes["current_temperature"]
        if "temperature" in attributes:
            values["TargetTemperature"] = attributes["temperature"]
        return values
    if domain == "lock":
        return {"LockState": "LOCK" if raw_state == "locked" else "UNLOCK"}
    return {"State": raw_state}


def availability_from_state(state: dict[str, Any]) -> str:
    raw_state = state.get("state")
    if raw_state == "unavailable":
        return "offline"
    if raw_state in {None, "unknown"}:
        return "unknown"
    return "online"


def map_ha_state(
    state: dict[str, Any],
    *,
    device_id: str | None = None,
    registry_entry: dict[str, Any] | None = None,
    services: dict[str, Any] | None = None,
) -> ProviderDevice:
    entity_id = str(state["entity_id"])
    domain = _domain(state)
    attributes = state.get("attributes") or {}
    stable_ref = f"device:{device_id}" if device_id else f"entity:{entity_id}"
    registry_entry = registry_entry or {}
    registry_identity = str(
        registry_entry.get("id")
        or registry_entry.get("unique_id")
        or registry_entry.get("entity_id")
        or entity_id
    )
    bindings = property_bindings(domain, entity_id, registry_identity)
    bindings.extend(
        service_bindings(
            domain,
            entity_id,
            registry_identity,
            services or {},
        )
    )
    draft = ProviderDevice(
        external_ref=stable_ref,
        display_name=str(attributes.get("friendly_name", entity_id)),
        capability_fingerprint="",
        product_key="",
        product_name=f"Home Assistant {domain}",
        state=DeviceState(
            device_ref=stable_ref,
            values=properties_from_state(state),
            availability=availability_from_state(state),
        ),
        feature_bindings=bindings,
        metadata={
            "entity_ids": [entity_id],
            "registry_identities": [registry_identity],
            "virtual": device_id is None,
            "domain": domain,
        },
        risk_level="high" if domain == "lock" else "low",
    )
    fingerprint = capability_fingerprint(draft)
    return draft.model_copy(
        update={
            "capability_fingerprint": fingerprint,
            "product_key": f"ha-{fingerprint[7:23]}",
        }
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
        return (
            "switch",
            "turn_on" if values["PowerSwitch"] else "turn_off",
            payload,
        )
    if domain == "climate" and "TargetTemperature" in values:
        payload["temperature"] = values["TargetTemperature"]
        return "climate", "set_temperature", payload
    if domain == "climate" and "PowerSwitch" in values:
        if not isinstance(values["PowerSwitch"], bool):
            raise ValueError("PowerSwitch must be boolean")
        return (
            "climate",
            "turn_on" if values["PowerSwitch"] else "turn_off",
            payload,
        )
    if domain == "lock" and "LockState" in values:
        lock_state = values["LockState"]
        if not isinstance(lock_state, str) or lock_state not in {"LOCK", "UNLOCK"}:
            raise ValueError("LockState must be LOCK or UNLOCK")
        return "lock", "lock" if lock_state == "LOCK" else "unlock", payload
    raise ValueError(
        f"unsupported properties for {entity_id}: {sorted(values)}"
    )

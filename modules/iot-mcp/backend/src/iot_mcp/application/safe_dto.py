"""Canonical secret-safe DTOs for every inbound and outbound boundary."""

from __future__ import annotations

import json
import re
from typing import Any

from iot_mcp.domain.models import ControlOperation

REDACTED = "[REDACTED]"
_SENSITIVE_PARTS = {
    "authorization",
    "credential",
    "idempotency",
    "key",
    "password",
    "pin",
    "secret",
    "token",
}
_ACTION_SENSITIVE_PARTS = _SENSITIVE_PARTS | {"code"}


def _key_parts(key: str) -> set[str]:
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    parts = {
        part
        for part in re.split(r"[^a-z0-9]+", camel_split.lower())
        if part
    }
    normalized = "".join(character for character in key.lower() if character.isalnum())
    if normalized:
        parts.add(normalized)
    return parts


def is_sensitive_key(key: str) -> bool:
    parts = _key_parts(key)
    return bool(parts & _SENSITIVE_PARTS) or any(
        part.endswith(sensitive)
        for part in parts
        for sensitive in _SENSITIVE_PARTS
        if sensitive != "code"
    )


def _is_sensitive_action_key(key: str) -> bool:
    parts = _key_parts(key)
    return bool(parts & _ACTION_SENSITIVE_PARTS) or any(
        part.endswith(sensitive)
        for part in parts
        for sensitive in _ACTION_SENSITIVE_PARTS
        if sensitive != "code"
    )


def contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _is_sensitive_action_key(str(key))
            or contains_sensitive_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive_key(child) for child in value)
    return False


def redact_sensitive(value: Any) -> Any:
    """Recursively retain field names while replacing secret-bearing values."""
    return _redact_sensitive(value, action=False)


def _redact_sensitive(value: Any, *, action: bool) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        predicate = (
            _is_sensitive_action_key if action else is_sensitive_key
        )
        return {
            str(key): (
                REDACTED
                if predicate(str(key))
                else _redact_sensitive(child, action=action)
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _redact_sensitive(child, action=action) for child in value
        ]
    return value


def safe_action_dto(action: Any) -> dict[str, Any]:
    """Return the single typed action representation used by all boundaries."""
    if hasattr(action, "model_dump"):
        action = action.model_dump(mode="json")
    if not isinstance(action, dict):
        return {"kind": "unknown"}
    kind = action.get("kind")
    if kind == "properties":
        raw_values = action.get("values")
        values = raw_values if isinstance(raw_values, dict) else {}
        return {
            "kind": "properties",
            "values": {
                str(key): (
                    REDACTED
                    if _is_sensitive_action_key(str(key))
                    else _redact_sensitive(value, action=True)
                )
                for key, value in sorted(values.items(), key=lambda item: str(item[0]))
            },
        }
    if kind == "service":
        raw_inputs = action.get("inputs")
        inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
        return {
            "kind": "service",
            "service": str(action.get("service") or ""),
            "inputs": {
                str(key): (
                    REDACTED
                    if _is_sensitive_action_key(str(key))
                    else _redact_sensitive(value, action=True)
                )
                for key, value in sorted(inputs.items(), key=lambda item: str(item[0]))
            },
        }
    return {"kind": "unknown"}


def operation_public_dto(operation: ControlOperation) -> dict[str, Any]:
    """Serialize an operation without exposing its raw ledger or idempotency key."""
    return {
        "operation_id": operation.operation_id,
        "device_id": operation.device_id,
        "interaction_mode": operation.interaction_mode.value,
        "action": safe_action_dto(operation.action),
        "target": operation.external_device_ref or operation.device_id,
        "provider_id": operation.provider_id,
        "provider_type": operation.provider_type,
        "binding_revision": operation.binding_revision,
        "status": operation.status.value,
        "result": redact_sensitive(operation.result),
        "created_at": operation.created_at.isoformat(),
        "updated_at": operation.updated_at.isoformat(),
    }


def action_summary(action: Any) -> tuple[str, str]:
    safe = safe_action_dto(action)
    kind = safe["kind"]
    if kind == "properties":
        values = safe["values"]
        rendered = "、".join(
            f"{identifier}={_display_value(value)}"
            for identifier, value in values.items()
        )
        return kind, f"写入属性：{rendered}" if rendered else "写入属性"
    if kind == "service":
        inputs = safe["inputs"]
        rendered = "、".join(
            f"{identifier}={_display_value(value)}"
            for identifier, value in inputs.items()
        )
        prefix = f"调用服务 {safe['service'] or 'unknown'}"
        return kind, f"{prefix}：{rendered}" if rendered else prefix
    return "unknown", "未知动作"


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

"""Explicit, secret-safe DTOs for the browser operations console."""

from __future__ import annotations

from typing import Any

from iot_mcp.domain.models import ConfirmationRequest, ControlOperation, DeviceInstance

_SENSITIVE_KEY_PARTS = {
    "authorization",
    "credential",
    "idempotency",
    "key",
    "password",
    "pin",
    "secret",
    "token",
}


def redact_sensitive(value: Any) -> Any:
    """Recursively redact secret-bearing keys before deriving console output."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _is_sensitive_key(str(key))
                else redact_sensitive(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(child) for child in value]
    if isinstance(value, tuple):
        return [redact_sensitive(child) for child in value]
    return value


def operation_console_dto(
    operation: ControlOperation, device: DeviceInstance | None = None
) -> dict[str, Any]:
    action_kind, action_summary = _action_summary(operation.action)
    source_category, source_label = _source(operation)
    return {
        "operation_id": operation.operation_id,
        "device_id": operation.device_id,
        "source_category": source_category,
        "source_label": source_label,
        "action_kind": action_kind,
        "action_summary": action_summary,
        "sensitive_values_redacted": _contains_sensitive_key(operation.action),
        "target": operation.external_device_ref or operation.device_id,
        "provider_id": operation.provider_id,
        "provider_type": operation.provider_type,
        "binding_revision": operation.binding_revision,
        "risk_level": device.risk_level.value if device else "unknown",
        "status": operation.status.value,
        "created_at": operation.created_at.isoformat(),
        "updated_at": operation.updated_at.isoformat(),
    }


def confirmation_console_dto(
    confirmation: ConfirmationRequest,
    operation: ControlOperation | None,
    device: DeviceInstance | None = None,
) -> dict[str, Any]:
    return {
        "confirmation": {
            "confirmation_id": confirmation.confirmation_id,
            "operation_id": confirmation.operation_id,
            "action_hash": confirmation.action_hash,
            "target": confirmation.external_device_ref
            or (operation.device_id if operation else "unknown"),
            "provider_id": confirmation.provider_id,
            "provider_type": confirmation.provider_type,
            "binding_revision": confirmation.binding_revision,
            "expires_at": confirmation.expires_at.isoformat(),
            "decision": confirmation.decision.value,
            "created_at": confirmation.created_at.isoformat(),
            "risk_level": "high",
        },
        "operation": operation_console_dto(operation, device) if operation else None,
    }


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _is_sensitive_key(str(key)) or _contains_sensitive_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(child) for child in value)
    return False


def _action_summary(action: Any) -> tuple[str, str]:
    if not isinstance(action, dict):
        return "unknown", "未知动作"
    kind = str(action.get("kind") or action.get("type") or "unknown")
    if kind == "properties":
        values = action.get("values")
        identifiers = (
            sorted(str(key) for key in values)
            if isinstance(values, dict)
            else []
        )
        suffix = f"：{'、'.join(identifiers)}" if identifiers else ""
        return kind, f"写入 {len(identifiers)} 个属性{suffix}"
    if kind == "service":
        service = str(action.get("service") or action.get("identifier") or "unknown")
        inputs = action.get("inputs")
        identifiers = sorted(str(key) for key in inputs) if isinstance(inputs, dict) else []
        suffix = f"：{'、'.join(identifiers)}" if identifiers else ""
        return kind, f"调用服务 {service}（{len(identifiers)} 个参数{suffix}）"
    return "unknown", "未知动作"


def _source(operation: ControlOperation) -> tuple[str, str]:
    category = operation.interaction_mode.value
    if category == "human_interactive":
        return category, "Web operator"
    initiator = operation.initiator.lower()
    if initiator.startswith("mcp:"):
        return category, "MCP agent"
    if initiator.startswith("scheduler:"):
        return category, "Scheduler"
    if initiator.startswith("machine_token:"):
        return category, "Machine automation"
    return category, "Automation"

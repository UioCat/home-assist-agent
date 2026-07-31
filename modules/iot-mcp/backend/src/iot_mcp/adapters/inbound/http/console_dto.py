"""Explicit, secret-safe DTOs for the browser operations console."""

from __future__ import annotations

from typing import Any

from iot_mcp.application.safe_dto import (
    action_summary,
    contains_sensitive_key,
    operation_public_dto,
    redact_sensitive,
    safe_action_dto,
)
from iot_mcp.domain.models import ConfirmationRequest, ControlOperation, DeviceInstance

__all__ = [
    "confirmation_console_dto",
    "operation_console_dto",
    "operation_public_dto",
    "redact_sensitive",
    "safe_action_dto",
]


def operation_console_dto(
    operation: ControlOperation, device: DeviceInstance | None = None
) -> dict[str, Any]:
    action_kind, summary = action_summary(operation.action)
    source_category, source_label = _source(operation)
    return {
        "operation_id": operation.operation_id,
        "device_id": operation.device_id,
        "source_category": source_category,
        "source_label": source_label,
        "action_kind": action_kind,
        "action_summary": summary,
        "sensitive_values_redacted": contains_sensitive_key(operation.action),
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

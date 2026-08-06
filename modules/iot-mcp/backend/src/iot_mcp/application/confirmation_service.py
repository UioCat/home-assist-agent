"""Approval/rejection of an already persisted and cryptographically bound action."""

from __future__ import annotations

import hmac
from uuid import uuid4

from iot_mcp.adapters.outbound.persistence.repositories import (
    ConfirmationRepository,
    DeviceRepository,
    OperationRepository,
)
from iot_mcp.application.control_service import ControlService
from iot_mcp.application.policy import (
    BoundTarget,
    ControlAction,
    SafeControlError,
    canonical_action_hash,
)
from iot_mcp.domain.enums import ConfirmationDecision, OperationStatus
from iot_mcp.domain.models import (
    ConfirmationRequest,
    ControlOperation,
    ProviderDeviceBinding,
    utc_now,
)


class ConfirmationService:
    def __init__(
        self,
        *,
        devices: DeviceRepository,
        operations: OperationRepository,
        confirmations: ConfirmationRepository,
        control: ControlService,
    ) -> None:
        self._devices = devices
        self._operations = operations
        self._confirmations = confirmations
        self._control = control

    async def decide(
        self,
        *,
        confirmation_id: str,
        decision: str,
        actor: str,
        action_hash: str,
        message_id: str | None = None,
    ) -> ControlOperation:
        confirmation = await self._confirmations.get_request(confirmation_id)
        if confirmation is None:
            raise SafeControlError(
                "confirmation_not_found", "confirmation was not found", status_code=404
            )
        operation = await self._operations.get_operation(confirmation.operation_id)
        if operation is None:
            raise SafeControlError(
                "operation_not_found", "operation was not found", status_code=404
            )
        if not hmac.compare_digest(actor, confirmation.authorized_actor):
            raise SafeControlError(
                "actor_not_authorized", "actor is not authorized", status_code=403
            )
        if decision not in {"approve", "reject"}:
            raise SafeControlError(
                "invalid_decision", "decision must be approve or reject", status_code=422
            )
        if not hmac.compare_digest(action_hash, confirmation.action_hash):
            return await self._reject_bound_operation(
                confirmation_id, operation, code="action_hash_mismatch"
            )
        if confirmation.expires_at <= utc_now():
            decided = await self._confirmations.decide_pending(
                confirmation_id, ConfirmationDecision.EXPIRED
            )
            if decided is None:
                raise SafeControlError(
                    "confirmation_already_decided",
                    "confirmation was already decided",
                    status_code=409,
                )
            return await self._operations.update_operation(
                operation.operation_id,
                status=OperationStatus.EXPIRED,
                result={"code": "confirmation_expired", "retryable": False},
            )
        if decision == "reject":
            return await self._reject_bound_operation(
                confirmation_id, operation, code="confirmation_rejected"
            )

        action = ControlAction.model_validate(operation.action)
        target = _confirmed_target(operation, confirmation)
        if target is None:
            return await self._reject_bound_operation(
                confirmation_id, operation, code="bound_target_missing"
            )
        binding = await self._devices.get_primary_binding(
            operation.device_id, target.provider_id
        )
        expected_hash = canonical_action_hash(
            operation.device_id,
            action,
            target=target,
        )
        if not _binding_matches_target(binding, target) or not hmac.compare_digest(
            expected_hash, confirmation.action_hash
        ):
            return await self._reject_bound_operation(
                confirmation_id, operation, code="binding_changed"
            )
        decided = await self._confirmations.decide_pending(
            confirmation_id,
            ConfirmationDecision.APPROVED,
            expected_action_hash=confirmation.action_hash,
            expected_binding_id=target.binding_id,
            expected_binding_revision=target.binding_revision,
        )
        if decided is None:
            raise SafeControlError(
                "confirmation_already_decided",
                "confirmation was already decided",
                status_code=409,
            )
        return await self._control.execute_approved(
            operation.operation_id, message_id=message_id or str(uuid4())
        )

    async def _reject_bound_operation(
        self, confirmation_id: str, operation: ControlOperation, *, code: str
    ) -> ControlOperation:
        decided = await self._confirmations.decide_pending(
            confirmation_id, ConfirmationDecision.REJECTED
        )
        if decided is None:
            raise SafeControlError(
                "confirmation_already_decided",
                "confirmation was already decided",
                status_code=409,
            )
        return await self._operations.update_operation(
            operation.operation_id,
            status=OperationStatus.REJECTED,
            result={"code": code, "retryable": False},
        )


def _confirmed_target(
    operation: ControlOperation, confirmation: ConfirmationRequest
) -> BoundTarget | None:
    operation_values = {
        "binding_id": operation.binding_id,
        "provider_id": operation.provider_id,
        "provider_type": operation.provider_type,
        "external_device_ref": operation.external_device_ref,
        "binding_revision": operation.binding_revision,
    }
    confirmation_values = {
        key: getattr(confirmation, key, None) for key in operation_values
    }
    if (
        any(value is None for value in operation_values.values())
        or operation_values != confirmation_values
    ):
        return None
    return BoundTarget.model_validate(operation_values)


def _binding_matches_target(
    binding: ProviderDeviceBinding | None, target: BoundTarget
) -> bool:
    return binding is not None and all(
        getattr(binding, key, None) == value
        for key, value in target.model_dump().items()
    )

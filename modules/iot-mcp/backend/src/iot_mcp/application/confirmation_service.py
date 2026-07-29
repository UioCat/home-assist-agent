"""Approval/rejection of an already persisted and cryptographically bound action."""

from __future__ import annotations

import hmac

from iot_mcp.adapters.outbound.persistence.repositories import (
    ConfirmationRepository,
    DeviceRepository,
    OperationRepository,
)
from iot_mcp.application.control_service import ControlService
from iot_mcp.application.policy import ControlAction, SafeControlError, canonical_action_hash
from iot_mcp.domain.enums import ConfirmationDecision, OperationStatus
from iot_mcp.domain.models import ControlOperation, utc_now


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

        binding = await self._devices.get_primary_binding(operation.device_id)
        action = ControlAction.model_validate(operation.action)
        expected_hash = (
            canonical_action_hash(
                operation.device_id,
                action,
                binding_revision=binding.binding_revision,
            )
            if binding is not None
            else ""
        )
        if (
            binding is None
            or binding.binding_revision != confirmation.binding_revision
            or not hmac.compare_digest(expected_hash, confirmation.action_hash)
        ):
            return await self._reject_bound_operation(
                confirmation_id, operation, code="binding_changed"
            )
        decided = await self._confirmations.decide_pending(
            confirmation_id, ConfirmationDecision.APPROVED
        )
        if decided is None:
            raise SafeControlError(
                "confirmation_already_decided",
                "confirmation was already decided",
                status_code=409,
            )
        return await self._control.execute_approved(operation.operation_id)

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

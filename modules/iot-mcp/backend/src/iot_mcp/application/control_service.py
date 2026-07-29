"""Persist-first device-control orchestration."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError

from iot_mcp.adapters.outbound.persistence.repositories import (
    ConfirmationRepository,
    DeviceRepository,
    OperationRepository,
    ThingModelRepository,
)
from iot_mcp.application.policy import (
    ControlAction,
    ControlPolicy,
    SafeControlError,
    TrustedPrincipal,
    canonical_action_hash,
)
from iot_mcp.domain.enums import ModelStatus, OperationStatus, RiskLevel
from iot_mcp.domain.models import ConfirmationRequest, ControlOperation, utc_now
from iot_mcp.domain.tsl import TslDocument, TslValidationError
from iot_mcp.ports.device_provider import DeviceProvider, ProviderResult
from iot_mcp.ports.message_channel import MessageChannel

if TYPE_CHECKING:
    from iot_mcp.application.confirmation_service import ConfirmationService


class ControlService:
    def __init__(
        self,
        *,
        devices: DeviceRepository,
        operations: OperationRepository,
        confirmations: ConfirmationRepository,
        providers: dict[str, DeviceProvider],
        confirmation_actor: str,
        models: ThingModelRepository | None = None,
        message_channel: MessageChannel | None = None,
        confirmation_ttl_seconds: int = 300,
        policy: ControlPolicy | None = None,
    ) -> None:
        self._devices = devices
        self._operations = operations
        self._confirmations = confirmations
        self._providers = providers
        self._confirmation_actor = confirmation_actor
        self._models = models
        self._message_channel = message_channel
        self._confirmation_ttl_seconds = confirmation_ttl_seconds
        self._policy = policy or ControlPolicy()
        self._confirmation_service: ConfirmationService | None = None

    def bind_confirmation_service(self, service: ConfirmationService) -> None:
        self._confirmation_service = service

    async def submit(
        self,
        *,
        device_id: str,
        action: ControlAction,
        principal: TrustedPrincipal,
        idempotency_key: str,
    ) -> ControlOperation:
        if not idempotency_key:
            raise SafeControlError("idempotency_key_required", "Idempotency-Key is required")
        existing = await self._operations.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        device = await self._devices.get_device(device_id)
        if device is None:
            raise SafeControlError("target_not_found", "device was not found", status_code=404)
        binding = await self._devices.get_primary_binding(device_id)
        if binding is None:
            raise SafeControlError(
                "binding_not_found", "device binding was not found", status_code=409
            )
        await self._validate_action(device_id, device.product_id, action)
        risk = await self._resolve_risk(device_id, device.risk_level, action)

        proposed = ControlOperation(
            device_id=device_id,
            initiator=f"{principal.source}:{principal.actor_id}",
            interaction_mode=principal.mode,
            action=action.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        try:
            operation = await self._operations.create_operation(proposed)
        except Exception as error:
            raise SafeControlError(
                "audit_unavailable",
                "control audit storage is unavailable",
                status_code=503,
                retryable=True,
            ) from error
        if operation.operation_id != proposed.operation_id:
            return operation

        if self._policy.requires_confirmation(principal=principal, risk=risk):
            action_hash = canonical_action_hash(
                device_id, action, binding_revision=binding.binding_revision
            )
            try:
                confirmation = await self._confirmations.create_request(
                    ConfirmationRequest(
                        operation_id=operation.operation_id,
                        action_hash=action_hash,
                        authorized_actor=self._confirmation_actor,
                        binding_revision=binding.binding_revision,
                        expires_at=utc_now()
                        + timedelta(seconds=self._confirmation_ttl_seconds),
                    )
                )
                operation = await self._operations.update_operation(
                    operation.operation_id,
                    status=OperationStatus.PENDING_CONFIRMATION,
                    result={"confirmation_id": confirmation.confirmation_id},
                )
            except Exception as error:
                raise SafeControlError(
                    "audit_unavailable",
                    "confirmation audit storage is unavailable",
                    status_code=503,
                    retryable=True,
                ) from error
            await self._notify_confirmation_safely(confirmation, action)
            return operation
        return await self._execute(operation, binding.external_device_ref)

    async def execute_approved(self, operation_id: str) -> ControlOperation:
        operation = await self._operations.get_operation(operation_id)
        if operation is None:
            raise SafeControlError(
                "operation_not_found", "operation was not found", status_code=404
            )
        binding = await self._devices.get_primary_binding(operation.device_id)
        if binding is None:
            return await self._operations.update_operation(
                operation.operation_id,
                status=OperationStatus.REJECTED,
                result={"code": "binding_not_found", "retryable": False},
            )
        operation = await self._operations.update_operation(
            operation.operation_id, status=OperationStatus.APPROVED
        )
        return await self._execute(operation, binding.external_device_ref)

    async def _execute(
        self, operation: ControlOperation, external_device_ref: str
    ) -> ControlOperation:
        provider = self._providers.get(
            (await self._devices.get_device(operation.device_id)).provider_id  # type: ignore[union-attr]
        )
        if provider is None:
            return await self._operations.update_operation(
                operation.operation_id,
                status=OperationStatus.FAILED,
                result={"code": "provider_not_found", "retryable": False},
            )
        action = ControlAction.model_validate(operation.action)
        operation = await self._operations.update_operation(
            operation.operation_id,
            status=OperationStatus.EXECUTING,
            provider_request=self._provider_request(action),
        )
        try:
            if action.kind == "properties":
                current = await provider.read_state(
                    external_device_ref, list(action.values)
                )
                if all(current.values.get(key) == value for key, value in action.values.items()):
                    return await self._operations.update_operation(
                        operation.operation_id,
                        status=OperationStatus.NO_OP,
                        result={"state": current.values},
                    )
                result = await provider.write_properties(external_device_ref, action.values)
            else:
                result = await provider.invoke_service(
                    external_device_ref, action.service or "", action.inputs
                )
        except TimeoutError:
            return await self._operations.update_operation(
                operation.operation_id,
                status=OperationStatus.UNKNOWN,
                result={"code": "provider_timeout", "retryable": True},
            )
        except Exception:
            return await self._operations.update_operation(
                operation.operation_id,
                status=OperationStatus.UNKNOWN,
                result={"code": "provider_error", "retryable": True},
            )
        completed = await self._record_provider_result(operation, action, result)
        await self._notify_result_safely(completed)
        return completed

    async def _record_provider_result(
        self,
        operation: ControlOperation,
        action: ControlAction,
        provider_result: ProviderResult,
    ) -> ControlOperation:
        status = self._result_status(action, provider_result)
        result = (
            {"data": _redact(provider_result.data)}
            if provider_result.ok
            else {
                "code": provider_result.error_code or "provider_failed",
                "message": "provider rejected the operation",
                "retryable": False,
            }
        )
        return await self._operations.update_operation(
            operation.operation_id,
            status=status,
            provider_result={
                "ok": provider_result.ok,
                "error_code": provider_result.error_code,
                "data": _redact(provider_result.data),
            },
            result=result,
        )

    @staticmethod
    def _result_status(
        action: ControlAction, provider_result: ProviderResult
    ) -> OperationStatus:
        if not provider_result.ok:
            return OperationStatus.FAILED
        after = provider_result.data.get("after")
        if (
            action.kind == "properties"
            and isinstance(after, dict)
            and all(after.get(key) == value for key, value in action.values.items())
        ):
            return OperationStatus.SUCCEEDED
        return OperationStatus.ACCEPTED

    async def _validate_action(
        self, device_id: str, product_id: str | None, action: ControlAction
    ) -> None:
        if action.kind == "properties" and not action.values:
            raise SafeControlError(
                "invalid_action", "property values must not be empty", status_code=422
            )
        if action.kind == "service" and not action.service:
            raise SafeControlError("invalid_action", "service is required", status_code=422)
        if self._models is None or product_id is None:
            return
        versions = await self._models.list_model_versions(product_id)
        active = next((item for item in versions if item.status is ModelStatus.ACTIVE), None)
        if active is None:
            return
        try:
            document = TslDocument.model_validate(active.tsl_json)
            if action.kind == "properties":
                for identifier, value in action.values.items():
                    document.validate_property_write(identifier, value)
            else:
                document.validate_service_inputs(action.service or "", action.inputs)
        except (ValidationError, TslValidationError) as error:
            raise SafeControlError(
                "invalid_action", "action does not match the active thing model", status_code=422
            ) from error

    async def _resolve_risk(
        self, device_id: str, default: RiskLevel, action: ControlAction
    ) -> RiskLevel:
        identifiers = set(action.values) if action.kind == "properties" else {action.service}
        levels = [default]
        for binding in await self._devices.list_feature_bindings(device_id):
            if binding.identifier in identifiers and binding.risk_level is not None:
                levels.append(binding.risk_level)
        rank = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        return max(levels, key=rank.__getitem__)

    @staticmethod
    def _provider_request(action: ControlAction) -> dict[str, object]:
        if action.kind == "properties":
            return {"kind": action.kind, "values": action.values}
        return {"kind": action.kind, "service": action.service, "inputs": action.inputs}

    async def _notify_confirmation_safely(
        self, confirmation: ConfirmationRequest, action: ControlAction
    ) -> None:
        if self._message_channel is None:
            return
        try:
            await self._message_channel.send_confirmation(
                {
                    "confirmation_id": confirmation.confirmation_id,
                    "action_hash": confirmation.action_hash,
                    "expires_at": confirmation.expires_at.isoformat(),
                    "action": action.model_dump(mode="json"),
                }
            )
        except Exception:
            pass

    async def _notify_result_safely(self, operation: ControlOperation) -> None:
        if self._message_channel is None:
            return
        try:
            await self._message_channel.send_result(
                {
                    "operation_id": operation.operation_id,
                    "status": operation.status.value,
                    "result": operation.result,
                }
            )
        except Exception:
            pass


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(
                    word in key.lower()
                    for word in ("token", "secret", "password", "authorization")
                )
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value

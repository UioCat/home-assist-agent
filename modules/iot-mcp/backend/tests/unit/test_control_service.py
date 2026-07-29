from __future__ import annotations

import pytest

from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.adapters.outbound.persistence.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from iot_mcp.adapters.outbound.persistence.repositories import (
    ConfirmationRepository,
    DeviceRepository,
    OperationRepository,
)
from iot_mcp.application.confirmation_service import ConfirmationService
from iot_mcp.application.control_service import ControlService
from iot_mcp.application.policy import ControlAction, SafeControlError, TrustedPrincipal
from iot_mcp.domain.enums import OperationStatus, RiskLevel
from iot_mcp.domain.models import DeviceInstance, ProviderDeviceBinding
from iot_mcp.ports.device_provider import ProviderResult


class CountingProvider(MockDeviceProvider):
    def __init__(self, *, timeout: bool = False) -> None:
        super().__init__()
        self.calls = 0
        self.timeout = timeout

    async def write_properties(self, device_ref, values) -> ProviderResult:
        self.calls += 1
        if self.timeout:
            raise TimeoutError
        return await super().write_properties(device_ref, values)


async def _services(
    tmp_path, *, risk: RiskLevel, provider=None, confirmation_ttl_seconds: int = 300
):
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    await initialize_database(engine)
    sessions = create_session_factory(engine)
    devices = DeviceRepository(sessions)
    operations = OperationRepository(sessions)
    confirmations = ConfirmationRepository(sessions)
    provider = provider or CountingProvider()
    device = await devices.upsert_device(
        DeviceInstance(
            device_id="device-1",
            provider_id=provider.provider_id,
            display_name="Test device",
            risk_level=risk,
        )
    )
    await devices.upsert_binding(
        ProviderDeviceBinding(
            device_id=device.device_id,
            provider_type=provider.provider_type,
            external_device_ref=(
                "mock:lock:front_door" if risk is RiskLevel.HIGH else "mock:light:desk"
            ),
            binding_revision=7,
        )
    )
    control = ControlService(
        devices=devices,
        operations=operations,
        confirmations=confirmations,
        providers={provider.provider_id: provider},
        confirmation_actor="owner",
        confirmation_ttl_seconds=confirmation_ttl_seconds,
    )
    confirmation = ConfirmationService(
        devices=devices,
        operations=operations,
        confirmations=confirmations,
        control=control,
    )
    control.bind_confirmation_service(confirmation)
    return engine, provider, devices, operations, confirmations, control, confirmation


@pytest.mark.asyncio
async def test_autonomous_high_risk_persists_confirmation_without_provider_call(tmp_path) -> None:
    engine, provider, _, _, confirmations, control, _ = await _services(
        tmp_path, risk=RiskLevel.HIGH
    )

    operation = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.mcp("call-1"),
        idempotency_key="high-risk-1",
    )

    assert operation.status is OperationStatus.PENDING_CONFIRMATION
    confirmation = await confirmations.get_by_operation(operation.operation_id)
    assert confirmation is not None
    assert confirmation.binding_revision == 7
    assert provider.calls == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_human_high_risk_executes_without_confirmation(tmp_path) -> None:
    engine, provider, _, _, confirmations, control, _ = await _services(
        tmp_path, risk=RiskLevel.HIGH
    )

    operation = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.web_session("owner"),
        idempotency_key="human-high-risk",
    )

    assert operation.status is OperationStatus.SUCCEEDED
    assert await confirmations.get_by_operation(operation.operation_id) is None
    assert provider.calls == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_idempotency_and_no_op_do_not_repeat_provider_calls(tmp_path) -> None:
    engine, provider, _, _, _, control, _ = await _services(tmp_path, risk=RiskLevel.LOW)

    no_op = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"PowerSwitch": True}),
        principal=TrustedPrincipal.machine_token("agent"),
        idempotency_key="same-state",
    )
    first = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"PowerSwitch": False}),
        principal=TrustedPrincipal.machine_token("agent"),
        idempotency_key="turn-off",
    )
    duplicate = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"PowerSwitch": False}),
        principal=TrustedPrincipal.machine_token("agent"),
        idempotency_key="turn-off",
    )

    assert no_op.status is OperationStatus.NO_OP
    assert first.operation_id == duplicate.operation_id
    assert provider.calls == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_provider_timeout_is_unknown(tmp_path) -> None:
    provider = CountingProvider(timeout=True)
    engine, _, _, _, _, control, _ = await _services(
        tmp_path, risk=RiskLevel.LOW, provider=provider
    )

    operation = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"PowerSwitch": False}),
        principal=TrustedPrincipal.machine_token("agent"),
        idempotency_key="timeout",
    )

    assert operation.status is OperationStatus.UNKNOWN
    assert operation.result == {"code": "provider_timeout", "retryable": True}
    await engine.dispose()


@pytest.mark.asyncio
async def test_changed_binding_blocks_approved_action(tmp_path) -> None:
    engine, provider, devices, operations, confirmations, control, confirmation_service = (
        await _services(tmp_path, risk=RiskLevel.HIGH)
    )
    operation = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.mcp("call-2"),
        idempotency_key="revision-change",
    )
    pending = await confirmations.get_by_operation(operation.operation_id)
    assert pending is not None
    binding = (await devices.list_bindings("device-1"))[0]
    await devices.upsert_binding(binding.model_copy(update={"binding_revision": 8}))

    rejected = await confirmation_service.decide(
        confirmation_id=pending.confirmation_id,
        decision="approve",
        actor="owner",
        action_hash=pending.action_hash,
    )

    assert rejected.status is OperationStatus.REJECTED
    assert provider.calls == 0
    persisted = await operations.get_operation(operation.operation_id)
    assert persisted is not None
    assert persisted.status is OperationStatus.REJECTED
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_and_hash_mismatched_confirmations_never_execute(tmp_path) -> None:
    engine, provider, _, _, confirmations, control, confirmation_service = await _services(
        tmp_path, risk=RiskLevel.HIGH, confirmation_ttl_seconds=-1
    )
    expired_operation = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.mcp("expired"),
        idempotency_key="expired",
    )
    expired = await confirmations.get_by_operation(expired_operation.operation_id)
    assert expired is not None
    result = await confirmation_service.decide(
        confirmation_id=expired.confirmation_id,
        decision="approve",
        actor="owner",
        action_hash=expired.action_hash,
    )
    assert result.status is OperationStatus.EXPIRED
    assert provider.calls == 0
    await engine.dispose()

    engine, provider, _, _, confirmations, control, confirmation_service = await _services(
        tmp_path, risk=RiskLevel.HIGH
    )
    operation = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.mcp("hash"),
        idempotency_key="hash",
    )
    pending = await confirmations.get_by_operation(operation.operation_id)
    assert pending is not None
    result = await confirmation_service.decide(
        confirmation_id=pending.confirmation_id,
        decision="approve",
        actor="owner",
        action_hash="not-the-bound-hash",
    )
    assert result.status is OperationStatus.REJECTED
    assert provider.calls == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_audit_failure_is_fail_closed(tmp_path) -> None:
    engine, provider, devices, _, confirmations, _, _ = await _services(
        tmp_path, risk=RiskLevel.LOW
    )

    class UnwritableOperations:
        async def get_by_idempotency_key(self, key):
            return None

        async def create_operation(self, operation):
            raise OSError("database is read-only")

    control = ControlService(
        devices=devices,
        operations=UnwritableOperations(),
        confirmations=confirmations,
        providers={provider.provider_id: provider},
        confirmation_actor="owner",
    )

    with pytest.raises(SafeControlError, match="audit storage is unavailable"):
        await control.submit(
            device_id="device-1",
            action=ControlAction.properties({"PowerSwitch": False}),
            principal=TrustedPrincipal.machine_token("agent"),
            idempotency_key="audit-down",
        )
    assert provider.calls == 0
    await engine.dispose()

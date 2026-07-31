from __future__ import annotations

import pytest
from sqlalchemy import text

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
from iot_mcp.domain.enums import (
    ConfirmationDecision,
    ModelStatus,
    OperationStatus,
    RiskLevel,
)
from iot_mcp.domain.models import (
    DeviceInstance,
    ProviderDeviceBinding,
    ThingModelVersion,
    ThingProduct,
)
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
async def test_idempotency_key_reuse_with_different_semantics_fails_closed(tmp_path) -> None:
    engine, provider, _, _, confirmations, control, _ = await _services(
        tmp_path, risk=RiskLevel.HIGH
    )

    first = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.mcp("caller-1"),
        idempotency_key="semantic-key",
    )
    duplicate = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.mcp("caller-1"),
        idempotency_key="semantic-key",
    )

    with pytest.raises(SafeControlError) as action_conflict:
        await control.submit(
            device_id="device-1",
            action=ControlAction.properties({"LockState": "LOCK"}),
            principal=TrustedPrincipal.mcp("caller-1"),
            idempotency_key="semantic-key",
        )
    with pytest.raises(SafeControlError) as principal_conflict:
        await control.submit(
            device_id="device-1",
            action=ControlAction.properties({"LockState": "UNLOCK"}),
            principal=TrustedPrincipal.mcp("caller-2"),
            idempotency_key="semantic-key",
        )

    assert duplicate.operation_id == first.operation_id
    assert action_conflict.value.code == "idempotency_conflict"
    assert principal_conflict.value.code == "idempotency_conflict"
    assert len(await confirmations.list_requests()) == 1
    assert provider.calls == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_product_device_without_active_bound_model_fails_closed(tmp_path) -> None:
    engine, provider, devices, operations, confirmations, _, _ = await _services(
        tmp_path, risk=RiskLevel.LOW
    )
    sessions = create_session_factory(engine)
    from iot_mcp.adapters.outbound.persistence.repositories import ThingModelRepository

    models = ThingModelRepository(sessions)
    product = await models.upsert_product(
        ThingProduct(
            product_key="manual-model",
            name="Manual model",
            source="manual",
            capability_fingerprint="fingerprint",
        )
    )
    draft = await models.add_model_version(
        ThingModelVersion(
            product_id=product.product_id,
            version=1,
            status=ModelStatus.DRAFT,
            tsl_json={
                "schema": "https://iotx-tsl.aliyuncs.com/schema.json",
                "profile": {"productKey": "manual-model"},
                "properties": [],
                "services": [],
                "events": [],
            },
        )
    )
    device = await devices.get_device("device-1")
    assert device is not None
    await devices.upsert_device(
        device.model_copy(
            update={"product_id": product.product_id, "model_version_id": None}
        )
    )
    control = ControlService(
        devices=devices,
        operations=operations,
        confirmations=confirmations,
        providers={provider.provider_id: provider},
        confirmation_actor="owner",
        models=models,
    )

    with pytest.raises(SafeControlError) as unbound:
        await control.submit(
            device_id="device-1",
            action=ControlAction.properties({"PowerSwitch": False}),
            principal=TrustedPrincipal.web_session("owner"),
            idempotency_key="unbound-model",
        )
    rebound = await devices.get_device("device-1")
    assert rebound is not None
    await devices.upsert_device(
        rebound.model_copy(update={"model_version_id": draft.model_version_id})
    )
    with pytest.raises(SafeControlError) as draft_bound:
        await control.submit(
            device_id="device-1",
            action=ControlAction.properties({"PowerSwitch": False}),
            principal=TrustedPrincipal.web_session("owner"),
            idempotency_key="draft-model",
        )

    assert unbound.value.code == "model_binding_missing"
    assert draft_bound.value.code == "model_not_active"
    assert provider.calls == 0
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
    assert operation.result == {"code": "provider_timeout", "retryable": False}
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


@pytest.mark.asyncio
async def test_approval_race_executes_only_persisted_binding_target(tmp_path) -> None:
    engine, provider, devices, operations, confirmations, _, _ = await _services(
        tmp_path, risk=RiskLevel.HIGH
    )

    class BindingChangingConfirmations:
        def __getattr__(self, name):
            return getattr(confirmations, name)

        async def decide_pending(
            self, confirmation_id, decision, *, decided_at=None, **conditions
        ):
            result = await confirmations.decide_pending(
                confirmation_id,
                decision,
                decided_at=decided_at,
                **conditions,
            )
            if result is not None and decision is ConfirmationDecision.APPROVED:
                binding = await devices.get_primary_binding("device-1")
                await devices.upsert_binding(
                    binding.model_copy(
                        update={
                            "external_device_ref": "mock:light:desk",
                            "binding_revision": binding.binding_revision + 1,
                        }
                    )
                )
            return result

    racing_confirmations = BindingChangingConfirmations()
    control = ControlService(
        devices=devices,
        operations=operations,
        confirmations=racing_confirmations,
        providers={provider.provider_id: provider},
        confirmation_actor="owner",
    )
    confirmation_service = ConfirmationService(
        devices=devices,
        operations=operations,
        confirmations=racing_confirmations,
        control=control,
    )
    operation = await control.submit(
        device_id="device-1",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.mcp("race"),
        idempotency_key="approval-race",
    )
    pending = await confirmations.get_by_operation(operation.operation_id)
    assert pending is not None

    approved = await confirmation_service.decide(
        confirmation_id=pending.confirmation_id,
        decision="approve",
        actor="owner",
        action_hash=pending.action_hash,
    )

    lock_state = await provider.read_state("mock:lock:front_door")
    light_state = await provider.read_state("mock:light:desk")
    assert approved.status is OperationStatus.SUCCEEDED
    assert lock_state.values["LockState"] == "UNLOCK"
    assert "LockState" not in light_state.values
    await engine.dispose()


@pytest.mark.asyncio
async def test_control_selects_binding_matching_device_provider(tmp_path) -> None:
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'providers.db'}")
    await initialize_database(engine)
    sessions = create_session_factory(engine)
    devices = DeviceRepository(sessions)
    operations = OperationRepository(sessions)
    confirmations = ConfirmationRepository(sessions)
    provider = CountingProvider()
    await devices.upsert_device(
        DeviceInstance(
            device_id="multi-provider",
            provider_id="mock",
            display_name="Multi-provider door",
            risk_level=RiskLevel.HIGH,
        )
    )
    await devices.upsert_binding(
        ProviderDeviceBinding(
            device_id="multi-provider",
            provider_id="other",
            provider_type="other",
            external_device_ref="mock:light:desk",
        )
    )
    expected = await devices.upsert_binding(
        ProviderDeviceBinding(
            device_id="multi-provider",
            provider_id="mock",
            provider_type="mock",
            external_device_ref="mock:lock:front_door",
        )
    )
    control = ControlService(
        devices=devices,
        operations=operations,
        confirmations=confirmations,
        providers={"mock": provider},
        confirmation_actor="owner",
    )

    operation = await control.submit(
        device_id="multi-provider",
        action=ControlAction.properties({"LockState": "UNLOCK"}),
        principal=TrustedPrincipal.web_session("owner"),
        idempotency_key="provider-binding",
    )

    assert operation.status is OperationStatus.SUCCEEDED
    assert operation.binding_id == expected.binding_id
    assert (await provider.read_state("mock:lock:front_door")).values["LockState"] == "UNLOCK"
    assert "LockState" not in (await provider.read_state("mock:light:desk")).values
    await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_upgrade_adds_safe_binding_and_nonce_schema(tmp_path) -> None:
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    await initialize_database(engine)
    devices = DeviceRepository(create_session_factory(engine))
    await devices.upsert_device(
        DeviceInstance(
            device_id="legacy-device",
            provider_id="mock",
            display_name="Legacy device",
        )
    )
    await devices.upsert_binding(
        ProviderDeviceBinding(
            device_id="legacy-device",
            provider_type="mock",
            external_device_ref="mock:light:desk",
        )
    )
    async with engine.begin() as connection:
        await connection.execute(text("DROP INDEX uq_device_provider_binding"))
        await connection.execute(text("DROP INDEX ix_provider_device_bindings_provider_id"))
        await connection.execute(
            text("ALTER TABLE provider_device_bindings DROP COLUMN provider_id")
        )
        for table, columns in {
            "control_operations": (
                "binding_id",
                "provider_id",
                "provider_type",
                "external_device_ref",
                "binding_revision",
            ),
            "confirmation_requests": (
                "binding_id",
                "provider_id",
                "provider_type",
                "external_device_ref",
            ),
        }.items():
            for column in columns:
                await connection.execute(
                    text(f"ALTER TABLE {table} DROP COLUMN {column}")
                )
        await connection.execute(text("DROP TABLE webhook_nonces"))

    await initialize_database(engine)

    async with engine.connect() as connection:
        binding_columns = {
            row[1]
            for row in (
                await connection.execute(text("PRAGMA table_info(provider_device_bindings)"))
            ).all()
        }
        operation_columns = {
            row[1]
            for row in (
                await connection.execute(text("PRAGMA table_info(control_operations)"))
            ).all()
        }
        confirmation_columns = {
            row[1]
            for row in (
                await connection.execute(text("PRAGMA table_info(confirmation_requests)"))
            ).all()
        }
        nonce_table = (
            await connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'webhook_nonces'"
                )
            )
        ).scalar_one()
        migrated_provider_id = (
            await connection.execute(
                text(
                    "SELECT provider_id FROM provider_device_bindings "
                    "WHERE device_id = 'legacy-device'"
                )
            )
        ).scalar_one()
        binding_indexes = {
            row[1]: row[2]
            for row in (
                await connection.execute(
                    text("PRAGMA index_list(provider_device_bindings)")
                )
            ).all()
        }

    assert "provider_id" in binding_columns
    assert {
        "binding_id",
        "provider_id",
        "provider_type",
        "external_device_ref",
        "binding_revision",
    } <= operation_columns
    assert {
        "binding_id",
        "provider_id",
        "provider_type",
        "external_device_ref",
    } <= confirmation_columns
    assert nonce_table == "webhook_nonces"
    assert migrated_provider_id == "mock"
    assert binding_indexes["uq_device_provider_binding"] == 1
    await engine.dispose()

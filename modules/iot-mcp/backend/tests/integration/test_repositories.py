from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from iot_mcp.adapters.outbound.persistence.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from iot_mcp.adapters.outbound.persistence.repositories import (
    ConfirmationRepository,
    DeviceRepository,
    OperationRepository,
    StateRepository,
    ThingModelRepository,
)
from iot_mcp.domain.enums import ConfirmationDecision, InteractionMode, OperationStatus
from iot_mcp.domain.models import (
    ConfirmationRequest,
    ControlOperation,
    DeviceEvent,
    DeviceInstance,
    PropertySnapshot,
    ProviderDeviceBinding,
    ThingModelVersion,
    ThingProduct,
    utc_now,
)


@pytest.mark.asyncio
async def test_repositories_persist_structured_domain_records_and_idempotency(tmp_path) -> None:
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'iot.db'}")
    await initialize_database(engine)
    sessions = create_session_factory(engine)

    product_repo = ThingModelRepository(sessions)
    device_repo = DeviceRepository(sessions)
    state_repo = StateRepository(sessions)
    operation_repo = OperationRepository(sessions)
    confirmation_repo = ConfirmationRepository(sessions)
    product = await product_repo.upsert_product(
        ThingProduct(
            product_key="lamp-v1",
            name="Lamp",
            source="mock",
            capability_fingerprint="sha256:lamp",
        )
    )
    model = await product_repo.add_model_version(
        ThingModelVersion(
            product_id=product.product_id,
            version=1,
            tsl_json={"schema": "x", "profile": {"productKey": "lamp-v1"}},
        )
    )
    device = await device_repo.upsert_device(
        DeviceInstance(product_id=product.product_id, provider_id="mock", display_name="Desk lamp")
    )
    binding = await device_repo.upsert_binding(
        ProviderDeviceBinding(
            device_id=device.device_id,
            provider_type="mock",
            external_device_ref="lamp-01",
            route_data={"entity_id": "light.desk"},
        )
    )
    observed_at = utc_now()
    await state_repo.add_snapshot(
        PropertySnapshot(
            device_id=device.device_id,
            identifier="brightness",
            value={"percent": 50},
            observed_at=observed_at,
            source="mock",
        )
    )
    await state_repo.add_event(
        DeviceEvent(
            device_id=device.device_id,
            identifier="overheated",
            type="alert",
            output_data={"temperature": 80.5},
            occurred_at=observed_at,
            source="mock",
        )
    )
    operation = ControlOperation(
        device_id=device.device_id,
        initiator="mcp:test",
        interaction_mode=InteractionMode.AUTONOMOUS,
        action={"kind": "set_properties", "values": {"brightness": 50}},
        idempotency_key="request-123",
        provider_request={"service": "turn_on"},
        provider_result={"accepted": True},
    )
    created = await operation_repo.create_operation(operation)
    duplicate = await operation_repo.create_operation(
        operation.model_copy(update={"operation_id": "other"})
    )
    confirmation = await confirmation_repo.create_request(
        ConfirmationRequest(
            operation_id=created.operation_id,
            action_hash="abc123",
            authorized_actor="admin",
            expires_at=utc_now() + timedelta(minutes=5),
        )
    )
    decided = await confirmation_repo.decide(
        confirmation.confirmation_id, ConfirmationDecision.APPROVED
    )

    assert model.tsl_json["profile"]["productKey"] == "lamp-v1"
    assert binding.route_data == {"entity_id": "light.desk"}
    assert (await state_repo.latest_snapshots(device.device_id))[0].observed_at.tzinfo is not None
    assert (await state_repo.list_events(device.device_id))[0].output_data == {"temperature": 80.5}
    assert duplicate.operation_id == created.operation_id
    assert decided.decision is ConfirmationDecision.APPROVED
    persisted_operation = await operation_repo.get_by_idempotency_key("request-123")
    assert persisted_operation is not None
    assert persisted_operation.status is OperationStatus.REQUESTED
    completed = await operation_repo.update_operation(
        created.operation_id, status=OperationStatus.SUCCEEDED
    )
    assert completed.provider_request == {"service": "turn_on"}
    assert completed.provider_result == {"accepted": True}
    cleared = await operation_repo.update_operation(
        created.operation_id, status=OperationStatus.SUCCEEDED, provider_request=None
    )
    assert cleared.provider_request is None

    async with engine.connect() as connection:
        journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
        foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1
    await engine.dispose()


def test_domain_rejects_naive_persistence_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PropertySnapshot(
            device_id="device-1",
            identifier="power",
            value=True,
            observed_at=datetime(2026, 1, 1),
            source="mock",
        )

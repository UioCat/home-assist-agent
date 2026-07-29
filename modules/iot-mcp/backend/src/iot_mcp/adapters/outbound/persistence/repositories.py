"""Async repositories for the IoT MCP domain objects."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iot_mcp.adapters.outbound.persistence.tables import (
    ConfirmationRequestTable,
    ControlOperationTable,
    DeviceEventTable,
    DeviceInstanceTable,
    FeatureBindingTable,
    PropertySnapshotTable,
    ProviderDeviceBindingTable,
    ThingModelVersionTable,
    ThingProductTable,
)
from iot_mcp.domain.enums import ConfirmationDecision, OperationStatus
from iot_mcp.domain.models import (
    ConfirmationRequest,
    ControlOperation,
    DeviceEvent,
    DeviceInstance,
    FeatureBinding,
    PropertySnapshot,
    ProviderDeviceBinding,
    ThingModelVersion,
    ThingProduct,
    utc_now,
)


def _values(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="python")


_UNSET = object()


class ThingModelRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_product(self, product: ThingProduct) -> ThingProduct:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ThingProductTable).where(
                    ThingProductTable.product_key == product.product_key
                )
            )
            if row is None:
                row = ThingProductTable(**_values(product))
                session.add(row)
            else:
                row.name = product.name
                row.source = product.source
                row.capability_fingerprint = product.capability_fingerprint
            await session.commit()
            return ThingProduct.model_validate(row)

    async def get_product(self, product_id: str) -> ThingProduct | None:
        async with self._sessions() as session:
            row = await session.get(ThingProductTable, product_id)
            return ThingProduct.model_validate(row) if row else None

    async def get_product_by_key(self, product_key: str) -> ThingProduct | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ThingProductTable).where(ThingProductTable.product_key == product_key)
            )
            return ThingProduct.model_validate(row) if row else None

    async def list_products(self) -> list[ThingProduct]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ThingProductTable).order_by(ThingProductTable.name)
            )
            return [ThingProduct.model_validate(row) for row in rows]

    async def add_model_version(self, model: ThingModelVersion) -> ThingModelVersion:
        async with self._sessions() as session:
            session.add(ThingModelVersionTable(**_values(model)))
            await session.commit()
            return model

    async def get_model_version(self, model_version_id: str) -> ThingModelVersion | None:
        async with self._sessions() as session:
            row = await session.get(ThingModelVersionTable, model_version_id)
            return ThingModelVersion.model_validate(row) if row else None

    async def list_model_versions(self, product_id: str) -> list[ThingModelVersion]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ThingModelVersionTable)
                .where(ThingModelVersionTable.product_id == product_id)
                .order_by(ThingModelVersionTable.version.desc())
            )
            return [ThingModelVersion.model_validate(row) for row in rows]


class DeviceRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_device(self, device: DeviceInstance) -> DeviceInstance:
        async with self._sessions() as session:
            row = await session.get(DeviceInstanceTable, device.device_id)
            if row is None:
                row = DeviceInstanceTable(**_values(device))
                session.add(row)
            else:
                for key, value in _values(device).items():
                    if key not in {"device_id", "created_at"}:
                        setattr(row, key, value)
            await session.commit()
            return DeviceInstance.model_validate(row)

    async def get_device(self, device_id: str) -> DeviceInstance | None:
        async with self._sessions() as session:
            row = await session.get(DeviceInstanceTable, device_id)
            return DeviceInstance.model_validate(row) if row else None

    async def list_devices(self) -> list[DeviceInstance]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(DeviceInstanceTable).order_by(DeviceInstanceTable.display_name)
            )
            return [DeviceInstance.model_validate(row) for row in rows]

    async def upsert_binding(self, binding: ProviderDeviceBinding) -> ProviderDeviceBinding:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ProviderDeviceBindingTable).where(
                    ProviderDeviceBindingTable.provider_type == binding.provider_type,
                    ProviderDeviceBindingTable.external_device_ref == binding.external_device_ref,
                )
            )
            if row is None:
                row = await session.scalar(
                    select(ProviderDeviceBindingTable).where(
                        ProviderDeviceBindingTable.device_id == binding.device_id,
                        ProviderDeviceBindingTable.provider_type == binding.provider_type,
                    )
                )
            if row is None:
                row = ProviderDeviceBindingTable(**_values(binding))
                session.add(row)
            else:
                route_changed = (
                    row.external_device_ref != binding.external_device_ref
                    or row.route_data != binding.route_data
                )
                next_revision = max(
                    binding.binding_revision,
                    row.binding_revision + (1 if route_changed else 0),
                )
                for key, value in _values(binding).items():
                    if key not in {"binding_id", "binding_revision", "created_at"}:
                        setattr(row, key, value)
                row.binding_revision = next_revision
            await session.commit()
            return ProviderDeviceBinding.model_validate(row)

    async def list_bindings(self, device_id: str) -> list[ProviderDeviceBinding]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ProviderDeviceBindingTable).where(
                    ProviderDeviceBindingTable.device_id == device_id
                )
            )
            return [ProviderDeviceBinding.model_validate(row) for row in rows]

    async def get_primary_binding(self, device_id: str) -> ProviderDeviceBinding | None:
        bindings = await self.list_bindings(device_id)
        return bindings[0] if bindings else None

    async def upsert_feature_binding(self, binding: FeatureBinding) -> FeatureBinding:
        async with self._sessions() as session:
            row = await session.scalar(
                select(FeatureBindingTable).where(
                    FeatureBindingTable.device_id == binding.device_id,
                    FeatureBindingTable.feature_type == binding.feature_type,
                    FeatureBindingTable.identifier == binding.identifier,
                )
            )
            if row is None:
                row = FeatureBindingTable(**_values(binding))
                session.add(row)
            else:
                for key, value in _values(binding).items():
                    if key not in {"feature_binding_id", "created_at"}:
                        setattr(row, key, value)
            await session.commit()
            return FeatureBinding.model_validate(row)

    async def list_feature_bindings(self, device_id: str) -> list[FeatureBinding]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(FeatureBindingTable).where(FeatureBindingTable.device_id == device_id)
            )
            return [FeatureBinding.model_validate(row) for row in rows]


class StateRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add_snapshot(self, snapshot: PropertySnapshot) -> PropertySnapshot:
        async with self._sessions() as session:
            session.add(PropertySnapshotTable(**_values(snapshot)))
            await session.commit()
            return snapshot

    async def latest_snapshots(self, device_id: str) -> list[PropertySnapshot]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(PropertySnapshotTable)
                .where(PropertySnapshotTable.device_id == device_id)
                .order_by(
                    PropertySnapshotTable.identifier,
                    PropertySnapshotTable.observed_at.desc(),
                )
            )
            latest: dict[str, PropertySnapshot] = {}
            for row in rows:
                latest.setdefault(row.identifier, PropertySnapshot.model_validate(row))
            return list(latest.values())

    async def add_event(self, event: DeviceEvent) -> DeviceEvent:
        async with self._sessions() as session:
            session.add(DeviceEventTable(**_values(event)))
            await session.commit()
            return event

    async def list_events(self, device_id: str, *, limit: int = 100) -> list[DeviceEvent]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(DeviceEventTable)
                .where(DeviceEventTable.device_id == device_id)
                .order_by(DeviceEventTable.occurred_at.desc())
                .limit(limit)
            )
            return [DeviceEvent.model_validate(row) for row in rows]


class OperationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_by_idempotency_key(self, key: str) -> ControlOperation | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ControlOperationTable).where(ControlOperationTable.idempotency_key == key)
            )
            return ControlOperation.model_validate(row) if row else None

    async def create_operation(self, operation: ControlOperation) -> ControlOperation:
        existing = await self.get_by_idempotency_key(operation.idempotency_key)
        if existing:
            return existing
        async with self._sessions() as session:
            session.add(ControlOperationTable(**_values(operation)))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                row = await session.scalar(
                    select(ControlOperationTable).where(
                        ControlOperationTable.idempotency_key == operation.idempotency_key
                    )
                )
                if row is not None:
                    return ControlOperation.model_validate(row)
                raise
            return operation

    async def get_operation(self, operation_id: str) -> ControlOperation | None:
        async with self._sessions() as session:
            row = await session.get(ControlOperationTable, operation_id)
            return ControlOperation.model_validate(row) if row else None

    async def list_operations(self, *, limit: int = 100) -> list[ControlOperation]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ControlOperationTable)
                .order_by(ControlOperationTable.created_at.desc())
                .limit(limit)
            )
            return [ControlOperation.model_validate(row) for row in rows]

    async def update_operation(
        self,
        operation_id: str,
        *,
        status: OperationStatus,
        provider_request: dict[str, Any] | None | object = _UNSET,
        provider_result: dict[str, Any] | None | object = _UNSET,
        result: dict[str, Any] | None | object = _UNSET,
    ) -> ControlOperation:
        async with self._sessions() as session:
            row = await session.get(ControlOperationTable, operation_id)
            if row is None:
                raise KeyError(f"unknown operation: {operation_id}")
            row.status = status.value
            if provider_request is not _UNSET:
                row.provider_request = provider_request
            if provider_result is not _UNSET:
                row.provider_result = provider_result
            if result is not _UNSET:
                row.result = result
            row.updated_at = utc_now()
            await session.commit()
            return ControlOperation.model_validate(row)


class ConfirmationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_request(self, request: ConfirmationRequest) -> ConfirmationRequest:
        async with self._sessions() as session:
            session.add(ConfirmationRequestTable(**_values(request)))
            await session.commit()
            return request

    async def get_request(self, confirmation_id: str) -> ConfirmationRequest | None:
        async with self._sessions() as session:
            row = await session.get(ConfirmationRequestTable, confirmation_id)
            return ConfirmationRequest.model_validate(row) if row else None

    async def get_by_operation(self, operation_id: str) -> ConfirmationRequest | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ConfirmationRequestTable).where(
                    ConfirmationRequestTable.operation_id == operation_id
                )
            )
            return ConfirmationRequest.model_validate(row) if row else None

    async def decide(
        self,
        confirmation_id: str,
        decision: ConfirmationDecision,
        *,
        decided_at: datetime | None = None,
    ) -> ConfirmationRequest:
        if decision is ConfirmationDecision.PENDING:
            raise ValueError("a confirmation decision cannot remain pending")
        async with self._sessions() as session:
            row = await session.get(ConfirmationRequestTable, confirmation_id)
            if row is None:
                raise KeyError(f"unknown confirmation request: {confirmation_id}")
            row.decision = decision.value
            row.decided_at = decided_at or utc_now()
            await session.commit()
            return ConfirmationRequest.model_validate(row)

    async def decide_pending(
        self,
        confirmation_id: str,
        decision: ConfirmationDecision,
        *,
        decided_at: datetime | None = None,
    ) -> ConfirmationRequest | None:
        """Atomically transition a pending confirmation exactly once."""
        if decision is ConfirmationDecision.PENDING:
            raise ValueError("a confirmation decision cannot remain pending")
        async with self._sessions() as session:
            result = await session.execute(
                update(ConfirmationRequestTable)
                .where(
                    ConfirmationRequestTable.confirmation_id == confirmation_id,
                    ConfirmationRequestTable.decision == ConfirmationDecision.PENDING.value,
                )
                .values(
                    decision=decision.value,
                    decided_at=decided_at or utc_now(),
                )
            )
            await session.commit()
            if result.rowcount != 1:
                return None
            row = await session.get(ConfirmationRequestTable, confirmation_id)
            return ConfirmationRequest.model_validate(row) if row else None

"""SQLAlchemy tables for the IoT MCP domain ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC datetimes and always restore timezone-aware values from SQLite."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetimes persisted by iot_mcp must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class ThingProductTable(Base):
    __tablename__ = "thing_products"

    product_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(64))
    capability_fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ThingModelVersionTable(Base):
    __tablename__ = "thing_model_versions"
    __table_args__ = (UniqueConstraint("product_id", "version", name="uq_model_product_version"),)

    model_version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("thing_products.product_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    tsl_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DeviceInstanceTable(Base):
    __tablename__ = "device_instances"

    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("thing_products.product_id"), index=True
    )
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("thing_model_versions.model_version_id"), index=True
    )
    provider_id: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    area: Mapped[str | None] = mapped_column(String(255))
    risk_level: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProviderDeviceBindingTable(Base):
    __tablename__ = "provider_device_bindings"
    __table_args__ = (
        UniqueConstraint("provider_type", "external_device_ref", name="uq_provider_external_ref"),
        Index(
            "uq_device_provider_binding",
            "device_id",
            "provider_id",
            unique=True,
            sqlite_where=text("provider_id IS NOT NULL"),
        ),
    )

    binding_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device_instances.device_id"), index=True)
    provider_id: Mapped[str | None] = mapped_column(String(255), index=True)
    provider_type: Mapped[str] = mapped_column(String(64), index=True)
    external_device_ref: Mapped[str] = mapped_column(String(512))
    binding_revision: Mapped[int] = mapped_column(Integer)
    route_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class FeatureBindingTable(Base):
    __tablename__ = "feature_bindings"
    __table_args__ = (
        UniqueConstraint("device_id", "feature_type", "identifier", name="uq_feature"),
    )

    feature_binding_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device_instances.device_id"), index=True)
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("thing_model_versions.model_version_id"), index=True
    )
    feature_type: Mapped[str] = mapped_column(String(32))
    identifier: Mapped[str] = mapped_column(String(255))
    provider_selector: Mapped[dict[str, Any]] = mapped_column(JSON)
    read_binding: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    write_binding: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    transformer: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    risk_level: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PropertySnapshotTable(Base):
    __tablename__ = "property_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device_instances.device_id"), index=True)
    identifier: Mapped[str] = mapped_column(String(255), index=True)
    value: Mapped[Any] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    source: Mapped[str] = mapped_column(String(64))
    freshness: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DeviceEventTable(Base):
    __tablename__ = "device_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device_instances.device_id"), index=True)
    identifier: Mapped[str] = mapped_column(String(255), index=True)
    type: Mapped[str] = mapped_column(String(64))
    output_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    source: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ControlOperationTable(Base):
    __tablename__ = "control_operations"

    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device_instances.device_id"), index=True)
    initiator: Mapped[str] = mapped_column(String(255))
    interaction_mode: Mapped[str] = mapped_column(String(32))
    action: Mapped[dict[str, Any]] = mapped_column(JSON)
    binding_id: Mapped[str | None] = mapped_column(String(36))
    provider_id: Mapped[str | None] = mapped_column(String(255))
    provider_type: Mapped[str | None] = mapped_column(String(64))
    external_device_ref: Mapped[str | None] = mapped_column(String(512))
    binding_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    provider_request: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    provider_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ConfirmationRequestTable(Base):
    __tablename__ = "confirmation_requests"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("control_operations.operation_id"), unique=True, index=True
    )
    action_hash: Mapped[str] = mapped_column(String(128))
    authorized_actor: Mapped[str] = mapped_column(String(255))
    binding_id: Mapped[str | None] = mapped_column(String(36))
    provider_id: Mapped[str | None] = mapped_column(String(255))
    provider_type: Mapped[str | None] = mapped_column(String(64))
    external_device_ref: Mapped[str | None] = mapped_column(String(512))
    binding_revision: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WebhookNonceTable(Base):
    __tablename__ = "webhook_nonces"

    nonce: Mapped[str] = mapped_column(String(255), primary_key=True)
    signed_timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

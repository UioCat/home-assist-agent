"""Persistence-independent IoT domain objects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iot_mcp.domain.enums import (
    ConfirmationDecision,
    Freshness,
    InteractionMode,
    ModelStatus,
    OperationStatus,
    RiskLevel,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_validator("*", mode="after")
    @classmethod
    def normalize_datetimes(cls, value: Any, info: Any) -> Any:
        if info.field_name.endswith("_at") and value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{info.field_name} must be timezone-aware")
            return value.astimezone(UTC)
        return value


class ThingProduct(DomainModel):
    product_id: str = Field(default_factory=_new_id)
    product_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    capability_fingerprint: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class ThingModelVersion(DomainModel):
    model_version_id: str = Field(default_factory=_new_id)
    product_id: str
    version: int = Field(ge=1)
    status: ModelStatus = ModelStatus.DRAFT
    tsl_json: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)


class DeviceInstance(DomainModel):
    device_id: str = Field(default_factory=_new_id)
    product_id: str | None = None
    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    area: str | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProviderDeviceBinding(DomainModel):
    binding_id: str = Field(default_factory=_new_id)
    device_id: str
    provider_type: str = Field(min_length=1)
    external_device_ref: str = Field(min_length=1)
    binding_revision: int = Field(default=1, ge=1)
    route_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FeatureBinding(DomainModel):
    feature_binding_id: str = Field(default_factory=_new_id)
    device_id: str
    model_version_id: str | None = None
    feature_type: str = Field(min_length=1)
    identifier: str = Field(min_length=1)
    provider_selector: dict[str, Any] = Field(default_factory=dict)
    read_binding: dict[str, Any] | None = None
    write_binding: dict[str, Any] | None = None
    transformer: dict[str, Any] | None = None
    risk_level: RiskLevel | None = None
    created_at: datetime = Field(default_factory=utc_now)


class PropertySnapshot(DomainModel):
    snapshot_id: str = Field(default_factory=_new_id)
    device_id: str
    identifier: str = Field(min_length=1)
    value: Any
    observed_at: datetime
    source: str = Field(min_length=1)
    freshness: Freshness = Freshness.FRESH
    created_at: datetime = Field(default_factory=utc_now)


class DeviceEvent(DomainModel):
    event_id: str = Field(default_factory=_new_id)
    device_id: str
    identifier: str = Field(min_length=1)
    type: str = Field(min_length=1)
    output_data: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime
    source: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class ControlOperation(DomainModel):
    operation_id: str = Field(default_factory=_new_id)
    device_id: str
    initiator: str = Field(min_length=1)
    interaction_mode: InteractionMode
    action: dict[str, Any]
    status: OperationStatus = OperationStatus.REQUESTED
    idempotency_key: str = Field(min_length=1)
    provider_request: dict[str, Any] | None = None
    provider_result: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConfirmationRequest(DomainModel):
    confirmation_id: str = Field(default_factory=_new_id)
    operation_id: str
    action_hash: str = Field(min_length=1)
    authorized_actor: str = Field(min_length=1)
    binding_revision: int = Field(default=1, ge=1)
    expires_at: datetime
    decision: ConfirmationDecision = ConfirmationDecision.PENDING
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

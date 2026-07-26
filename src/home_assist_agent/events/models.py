from datetime import datetime
from enum import StrEnum
import hashlib
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class EventStatus(StrEnum):
    OBSERVED = "observed"
    DUPLICATE = "duplicate"
    TRIGGERED = "triggered"


class EventRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=256)
    event_type: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    subject_id: str = Field(min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=200)
    occurred_at: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    causation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @field_validator(
        "event_id",
        "event_type",
        "source",
        "subject_id",
        "location",
        "correlation_id",
        "causation_id",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @property
    def message_id(self) -> str:
        identity = f"{self.source}\0{self.event_id}".encode("utf-8")
        return f"event_{hashlib.sha256(identity).hexdigest()}"

    @property
    def active_correlation_id(self) -> str:
        return self.correlation_id or self.message_id


class EventResponse(BaseModel):
    message_id: str
    request_id: str
    status: EventStatus
    event_type: str
    rule_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def synchronize_message_id(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        message_id = normalized.get("message_id") or normalized.get("request_id")
        if message_id is not None:
            normalized["message_id"] = message_id
            normalized["request_id"] = message_id
        return normalized


class HouseholdContextEntry(BaseModel):
    subject_id: str
    event_type: str
    location: str | None
    attributes: dict[str, Any]
    source_message_id: str
    occurred_at: datetime
    updated_at: datetime


class DerivedDeviceIntent(BaseModel):
    rule_id: str
    prompt: str
    source_message_id: str
    correlation_id: str | None = None
    causation_id: str

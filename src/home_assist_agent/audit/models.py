from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, model_validator


class AuditEvent(BaseModel):
    event_id: str
    message_id: str
    request_id: str
    conversation_id: str | None = None
    sequence: int
    event_type: str
    service: str
    payload: Any
    status: str
    error_code: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def synchronize_request_id(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        message_id = normalized.get("message_id") or normalized.get("request_id")
        if message_id is not None:
            normalized["message_id"] = message_id
            normalized["request_id"] = message_id
        return normalized


class AuditMessageSummary(BaseModel):
    message_id: str
    request_id: str
    conversation_id: str | None = None
    command: str | None = None
    response: str | None = None
    input_type: Literal["message", "event"] = "message"
    correlation_id: str | None = None
    status: str
    event_count: int
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="before")
    @classmethod
    def synchronize_request_id(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        message_id = normalized.get("message_id") or normalized.get("request_id")
        if message_id is not None:
            normalized["message_id"] = message_id
            normalized["request_id"] = message_id
        return normalized

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class AuditEvent(BaseModel):
    event_id: str
    message_id: str
    sequence: int
    event_type: str
    service: str
    payload: Any
    status: str
    error_code: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    created_at: datetime


class AuditMessageSummary(BaseModel):
    message_id: str
    command: str | None = None
    response: str | None = None
    input_type: Literal["message", "event"] = "message"
    correlation_id: str | None = None
    status: str
    event_count: int
    started_at: datetime
    ended_at: datetime

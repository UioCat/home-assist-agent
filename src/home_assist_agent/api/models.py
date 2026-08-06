from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from home_assist_agent.commands.models import ReasoningLevel
from home_assist_agent.commands.models import CommandResponse


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=1000)
    reasoning: ReasoningLevel = "medium"
    message_id: str | None = Field(default=None, min_length=1, max_length=128)
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("command must not be blank")
        return normalized

    @field_validator("message_id")
    @classmethod
    def normalize_message_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("message_id must not be blank")
        return normalized

    @field_validator("conversation_id")
    @classmethod
    def normalize_conversation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("conversation_id must not be blank")
        return normalized


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("message_id")
    @classmethod
    def normalize_message_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("message_id must not be blank")
        return normalized


class ConversationMessage(BaseModel):
    message_id: str
    request_id: str
    channel: str
    command: str
    status: str
    response: CommandResponse | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ConversationView(BaseModel):
    conversation_id: str
    status: str
    messages: list[ConversationMessage] = Field(default_factory=list)


class ConversationCreated(BaseModel):
    message_id: str
    request_id: str
    conversation_id: str
    status: str


class CodexHealth(BaseModel):
    installed: bool
    authenticated: bool
    error_code: str | None = None


class HaMcpHealth(BaseModel):
    configured: bool
    connected: bool
    tool_count: int
    error_code: str | None = None


class HealthResponse(BaseModel):
    backend: Literal["online"]
    codex: CodexHealth
    ha_mcp: HaMcpHealth

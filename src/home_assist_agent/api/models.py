from typing import Literal

from pydantic import BaseModel, Field, field_validator

from home_assist_agent.commands.models import ReasoningLevel


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=1000)
    reasoning: ReasoningLevel = "medium"

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("command must not be blank")
        return normalized


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

from enum import StrEnum
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class CommandCategory(StrEnum):
    DIRECT_IOT = "direct_iot"
    INDIRECT_IOT = "indirect_iot"
    OTHER = "other"


class CommandStatus(StrEnum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    ERROR = "error"


ReasoningLevel = Literal["low", "medium", "high"]


class ToolPlan(BaseModel):
    tool_name: str
    arguments_json: str

    @model_validator(mode="before")
    @classmethod
    def encode_internal_arguments(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and "arguments" in value
            and "arguments_json" not in value
        ):
            normalized = dict(value)
            arguments = normalized.pop("arguments")
            normalized["arguments_json"] = json.dumps(
                arguments,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return normalized
        return value

    @field_validator("arguments_json")
    @classmethod
    def require_json_object(cls, value: str) -> str:
        try:
            arguments = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("arguments_json must be valid JSON") from error
        if not isinstance(arguments, dict):
            raise ValueError("arguments_json must encode a JSON object")
        return value

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self.arguments_json)


class CodexRouteResult(BaseModel):
    category: Literal["indirect_iot", "other"]
    message: str
    tool_plan: ToolPlan | None = None


class ToolDefinition(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    tool_name: str
    content: str


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: str


class TraceStep(BaseModel):
    stage: str
    status: CommandStatus
    summary: str


class CommandResponse(BaseModel):
    request_id: str
    category: CommandCategory
    route: Literal["home_assistant_mcp", "codex"]
    status: CommandStatus
    message: str
    tool_call: ToolCallRecord | None = None
    trace: list[TraceStep] = Field(default_factory=list)
    elapsed_ms: int = 0
    error_code: str | None = None

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


class DeviceCommand(BaseModel):
    action: Literal["turn_on", "turn_off", "set_brightness"]
    target_expression: str = Field(min_length=1, max_length=200)
    parameters_json: str = "{}"

    @model_validator(mode="before")
    @classmethod
    def encode_internal_parameters(cls, value: Any) -> Any:
        if isinstance(value, dict):
            normalized = dict(value)
            if (
                "target" in normalized
                and "target_expression" not in normalized
            ):
                normalized["target_expression"] = normalized.pop("target")
            if (
                "parameters" not in normalized
                or "parameters_json" in normalized
            ):
                return normalized
            parameters = normalized.pop("parameters")
            normalized["parameters_json"] = json.dumps(
                parameters,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return normalized
        return value

    @field_validator("parameters_json")
    @classmethod
    def require_json_object(cls, value: str) -> str:
        try:
            parameters = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("parameters_json must be valid JSON") from error
        if not isinstance(parameters, dict):
            raise ValueError("parameters_json must encode a JSON object")
        return value

    @model_validator(mode="after")
    def validate_action_parameters(self) -> "DeviceCommand":
        brightness = self.parameters.get("brightness")
        if self.action == "set_brightness":
            if (
                isinstance(brightness, bool)
                or not isinstance(brightness, int)
                or not 0 <= brightness <= 100
            ):
                raise ValueError(
                    "set_brightness requires integer brightness from 0 to 100"
                )
        elif brightness is not None:
            raise ValueError("brightness is only valid for set_brightness")
        return self

    @property
    def parameters(self) -> dict[str, Any]:
        return json.loads(self.parameters_json)

    @property
    def target(self) -> str:
        return self.target_expression


class RouteDecision(BaseModel):
    category: CommandCategory
    device_command: DeviceCommand | None = None
    intent_summary: str | None = None
    target_expression: str | None = None

    @model_validator(mode="after")
    def validate_category_payload(self) -> "RouteDecision":
        if self.category == CommandCategory.DIRECT_IOT:
            if self.device_command is None:
                raise ValueError("direct_iot requires device_command")
        elif self.device_command is not None:
            raise ValueError("device_command is only valid for direct_iot")

        if self.category == CommandCategory.INDIRECT_IOT:
            if not self.intent_summary or not self.intent_summary.strip():
                raise ValueError("indirect_iot requires intent_summary")
            if not self.target_expression or not self.target_expression.strip():
                raise ValueError("indirect_iot requires target_expression")
        elif self.intent_summary is not None:
            raise ValueError("intent_summary is only valid for indirect_iot")
        if (
            self.category != CommandCategory.INDIRECT_IOT
            and self.target_expression is not None
        ):
            raise ValueError(
                "top-level target_expression is only valid for indirect_iot"
            )
        return self


class DevicePlanResult(BaseModel):
    message: str
    tool_plan: ToolPlan


class AnswerResult(BaseModel):
    message: str


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


class DeviceExecutionFailure(BaseModel):
    entity_id: str
    error_code: str
    message: str


class DeviceExecutionBatch(BaseModel):
    completed: tuple[str, ...] = ()
    failed: tuple[DeviceExecutionFailure, ...] = ()
    skipped: tuple[str, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()

    @property
    def fully_succeeded(self) -> bool:
        return bool(self.completed) and not self.failed and not self.skipped

    @property
    def learning_eligible(self) -> bool:
        return self.fully_succeeded


class TraceStep(BaseModel):
    stage: str
    status: CommandStatus
    summary: str


class CommandResponse(BaseModel):
    message_id: str
    request_id: str
    category: CommandCategory
    route: Literal["home_assistant_mcp", "codex"]
    status: CommandStatus
    message: str
    tool_call: ToolCallRecord | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    elapsed_ms: int = 0
    error_code: str | None = None

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
        tool_call = normalized.get("tool_call")
        tool_calls = normalized.get("tool_calls")
        if tool_call is not None and not tool_calls:
            normalized["tool_calls"] = [tool_call]
        elif isinstance(tool_calls, (list, tuple)):
            if len(tool_calls) == 1 and tool_call is None:
                normalized["tool_call"] = tool_calls[0]
            elif len(tool_calls) != 1:
                normalized["tool_call"] = None
        return normalized

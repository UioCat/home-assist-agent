from typing import Protocol

from jsonschema import validate
from jsonschema.exceptions import SchemaError, ValidationError

from home_assist_agent.commands.models import (
    DeviceExecutionBatch,
    DeviceExecutionFailure,
    DeviceCommand,
    ToolCallRecord,
    ToolDefinition,
    ToolExecutionResult,
    ToolPlan,
)
from home_assist_agent.ha.safety import SafetyPolicy
from home_assist_agent.resolution.models import (
    DeviceActionIntent,
    VerifiedTarget,
)


class HaMcpClientProtocol(Protocol):
    async def list_tools(
        self,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[ToolDefinition]: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ToolExecutionResult: ...


class DeviceExecutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DeviceExecutor:
    _action_tools = {
        "turn_on": "HassTurnOn",
        "turn_off": "HassTurnOff",
        "set_brightness": "HassLightSet",
    }

    def __init__(
        self,
        ha_mcp: HaMcpClientProtocol,
        safety: SafetyPolicy | None = None,
    ) -> None:
        self._ha_mcp = ha_mcp
        self._safety = safety or SafetyPolicy()

    async def list_safe_tools(
        self,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[ToolDefinition]:
        tools = await self._ha_mcp.list_tools(
            message_id,
            correlation_id,
            causation_id,
        )
        safe_names = set(self._safety.filter_tool_names([tool.name for tool in tools]))
        return [tool for tool in tools if tool.name in safe_names]

    async def execute_direct(
        self,
        command: DeviceCommand,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ToolCallRecord:
        arguments: dict[str, object] = {"name": command.target}
        if command.action == "set_brightness":
            arguments["brightness"] = command.parameters["brightness"]
        tools = await self.list_safe_tools(
            message_id,
            correlation_id,
            causation_id,
        )
        return await self._execute(
            requested_name=self._action_tools[command.action],
            arguments=arguments,
            tools=tools,
            message_id=message_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    async def execute_verified(
        self,
        *,
        intent: DeviceActionIntent,
        target: VerifiedTarget,
        message_id: str,
        plan: ToolPlan | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> DeviceExecutionBatch:
        if intent.action != target.action:
            raise ValueError("intent action must match verified target action")

        requested_name = self._action_tools[intent.action]
        if plan is not None:
            planned_suffix = plan.tool_name.rsplit(".", maxsplit=1)[-1]
            if planned_suffix != requested_name:
                raise DeviceExecutionError(
                    "plan_action_mismatch",
                    "设备计划动作与已验证动作不一致。",
                )
        base_arguments = dict(intent.parameters)
        if plan is not None:
            base_arguments.update(self._without_target_fields(plan.arguments))

        tools = await self.list_safe_tools(
            message_id,
            correlation_id,
            causation_id,
        )
        completed: list[str] = []
        failed: list[DeviceExecutionFailure] = []
        tool_calls: list[ToolCallRecord] = []
        ordered_entity_ids = tuple(sorted(target.entity_ids))
        for index, entity_id in enumerate(ordered_entity_ids):
            arguments: dict[str, object] = dict(base_arguments)
            arguments["name"] = entity_id
            try:
                tool_call = await self._execute(
                    requested_name=requested_name,
                    arguments=arguments,
                    tools=tools,
                    message_id=message_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
            except Exception as error:
                failed.append(
                    DeviceExecutionFailure(
                        entity_id=entity_id,
                        error_code=getattr(
                            error,
                            "code",
                            error.__class__.__name__,
                        ),
                        message=getattr(error, "message", str(error)),
                    )
                )
                return DeviceExecutionBatch(
                    completed=tuple(completed),
                    failed=tuple(failed),
                    skipped=ordered_entity_ids[index + 1 :],
                    tool_calls=tuple(tool_calls),
                )
            completed.append(entity_id)
            tool_calls.append(tool_call)
        return DeviceExecutionBatch(
            completed=tuple(completed),
            tool_calls=tuple(tool_calls),
        )

    async def execute_plan(
        self,
        plan: ToolPlan,
        tools: list[ToolDefinition],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ToolCallRecord:
        return await self._execute(
            requested_name=plan.tool_name,
            arguments=plan.arguments,
            tools=tools,
            message_id=message_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    async def _execute(
        self,
        *,
        requested_name: str,
        arguments: dict[str, object],
        tools: list[ToolDefinition],
        message_id: str,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> ToolCallRecord:
        resolved_name = self._safety.resolve_tool(
            requested_name=requested_name,
            arguments=arguments,
            available_tool_names=[tool.name for tool in tools],
        )
        tool_definition = next(tool for tool in tools if tool.name == resolved_name)
        try:
            validate(
                instance=arguments,
                schema=tool_definition.input_schema,
            )
        except (ValidationError, SchemaError) as error:
            raise DeviceExecutionError(
                "invalid_tool_arguments",
                "工具参数不符合 Home Assistant MCP 的实时定义。",
            ) from error
        result = await self._ha_mcp.call_tool(
            resolved_name,
            arguments,
            message_id,
            correlation_id,
            causation_id,
        )
        return ToolCallRecord(
            name=result.tool_name,
            arguments=arguments,
            result=result.content,
        )

    @classmethod
    def _without_target_fields(
        cls,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        forbidden = {
            "area",
            "domain",
            "entity_id",
            "floor",
            "name",
            "target",
            "target_expression",
        }

        def clean(value: object) -> object:
            if isinstance(value, dict):
                return {
                    key: clean(item)
                    for key, item in value.items()
                    if str(key).casefold() not in forbidden
                }
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value

        return clean(arguments)  # type: ignore[return-value]

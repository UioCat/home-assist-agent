from typing import Protocol

from jsonschema import validate
from jsonschema.exceptions import SchemaError, ValidationError

from home_assist_agent.commands.models import (
    DeviceCommand,
    ToolCallRecord,
    ToolDefinition,
    ToolExecutionResult,
    ToolPlan,
)
from home_assist_agent.ha.safety import SafetyPolicy


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

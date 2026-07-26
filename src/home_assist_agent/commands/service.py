from time import perf_counter
from typing import Protocol
from uuid import uuid4

from jsonschema import validate
from jsonschema.exceptions import SchemaError, ValidationError

from home_assist_agent.commands.classifier import DirectCommandParser
from home_assist_agent.commands.models import (
    CodexRouteResult,
    CommandCategory,
    CommandResponse,
    CommandStatus,
    ReasoningLevel,
    ToolCallRecord,
    ToolDefinition,
    ToolExecutionResult,
    TraceStep,
)
from home_assist_agent.errors import DependencyError
from home_assist_agent.ha.safety import SafetyPolicy, SafetyViolation


class CodexGatewayProtocol(Protocol):
    async def route(
        self,
        command: str,
        reasoning: ReasoningLevel,
        tools: list[ToolDefinition],
    ) -> CodexRouteResult: ...


class HaMcpClientProtocol(Protocol):
    async def list_tools(self) -> list[ToolDefinition]: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> ToolExecutionResult: ...


class CommandService:
    def __init__(
        self,
        codex: CodexGatewayProtocol,
        ha_mcp: HaMcpClientProtocol,
        parser: DirectCommandParser | None = None,
        safety: SafetyPolicy | None = None,
    ) -> None:
        self._codex = codex
        self._ha_mcp = ha_mcp
        self._parser = parser or DirectCommandParser()
        self._safety = safety or SafetyPolicy()

    async def execute(
        self,
        command: str,
        reasoning: ReasoningLevel,
    ) -> CommandResponse:
        started_at = perf_counter()
        request_id = uuid4().hex
        trace = [
            TraceStep(stage="input", status="success", summary="收到指令")
        ]

        if direct_action := self._parser.parse(command):
            trace.append(
                TraceStep(
                    stage="classify",
                    status="success",
                    summary="直接 IoT",
                )
            )
            return await self._execute_tool(
                request_id=request_id,
                category=CommandCategory.DIRECT_IOT,
                requested_name=direct_action.tool_name,
                arguments=direct_action.arguments,
                message="Home Assistant 已处理该指令。",
                tools=None,
                trace=trace,
                started_at=started_at,
            )

        tools: list[ToolDefinition]
        ha_error: DependencyError | None = None
        try:
            tools = await self._ha_mcp.list_tools()
        except DependencyError as error:
            tools = []
            ha_error = error

        safe_names = set(
            self._safety.filter_tool_names([tool.name for tool in tools])
        )
        safe_tools = [tool for tool in tools if tool.name in safe_names]

        try:
            route_result = await self._codex.route(command, reasoning, safe_tools)
        except DependencyError as error:
            trace.append(
                TraceStep(
                    stage="dispatch",
                    status="error",
                    summary=error.message,
                )
            )
            return self._response(
                request_id=request_id,
                category=CommandCategory.OTHER,
                route="codex",
                status=CommandStatus.ERROR,
                message=error.message,
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )

        if route_result.category == "other":
            trace.extend(
                [
                    TraceStep(
                        stage="classify",
                        status="success",
                        summary="其他指令",
                    ),
                    TraceStep(
                        stage="dispatch",
                        status="success",
                        summary="本地 Codex",
                    ),
                ]
            )
            return self._response(
                request_id=request_id,
                category=CommandCategory.OTHER,
                route="codex",
                status=CommandStatus.SUCCESS,
                message=route_result.message,
                trace=trace,
                started_at=started_at,
            )

        trace.append(
            TraceStep(
                stage="classify",
                status="success",
                summary="间接 IoT",
            )
        )
        if route_result.tool_plan is None:
            return self._response(
                request_id=request_id,
                category=CommandCategory.INDIRECT_IOT,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message="Codex 没有返回可执行的工具计划。",
                trace=trace,
                started_at=started_at,
                error_code="invalid_codex_output",
            )
        if ha_error is not None:
            trace.append(
                TraceStep(
                    stage="dispatch",
                    status="error",
                    summary=ha_error.message,
                )
            )
            return self._response(
                request_id=request_id,
                category=CommandCategory.INDIRECT_IOT,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message=ha_error.message,
                trace=trace,
                started_at=started_at,
                error_code=ha_error.code,
            )

        return await self._execute_tool(
            request_id=request_id,
            category=CommandCategory.INDIRECT_IOT,
            requested_name=route_result.tool_plan.tool_name,
            arguments=route_result.tool_plan.arguments,
            message=route_result.message,
            tools=tools,
            trace=trace,
            started_at=started_at,
        )

    async def _execute_tool(
        self,
        *,
        request_id: str,
        category: CommandCategory,
        requested_name: str,
        arguments: dict[str, object],
        message: str,
        tools: list[ToolDefinition] | None,
        trace: list[TraceStep],
        started_at: float,
    ) -> CommandResponse:
        try:
            live_tools = tools if tools is not None else await self._ha_mcp.list_tools()
            resolved_name = self._safety.resolve_tool(
                requested_name=requested_name,
                arguments=arguments,
                available_tool_names=[tool.name for tool in live_tools],
            )
            tool_definition = next(
                tool for tool in live_tools if tool.name == resolved_name
            )
            try:
                validate(
                    instance=arguments,
                    schema=tool_definition.input_schema,
                )
            except (ValidationError, SchemaError):
                trace.append(
                    TraceStep(
                        stage="dispatch",
                        status="error",
                        summary="工具参数不符合实时 MCP schema",
                    )
                )
                return self._response(
                    request_id=request_id,
                    category=category,
                    route="home_assistant_mcp",
                    status=CommandStatus.ERROR,
                    message="工具参数不符合 Home Assistant MCP 的实时定义。",
                    trace=trace,
                    started_at=started_at,
                    error_code="invalid_tool_arguments",
                )
            result = await self._ha_mcp.call_tool(resolved_name, arguments)
        except SafetyViolation as error:
            trace.append(
                TraceStep(
                    stage="dispatch",
                    status="blocked",
                    summary=error.code,
                )
            )
            return self._response(
                request_id=request_id,
                category=category,
                route="home_assistant_mcp",
                status=CommandStatus.BLOCKED,
                message="该工具或目标不在 MVP 的安全执行范围内。",
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )
        except DependencyError as error:
            trace.append(
                TraceStep(
                    stage="dispatch",
                    status="error",
                    summary=error.message,
                )
            )
            return self._response(
                request_id=request_id,
                category=category,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message=error.message,
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )

        trace.extend(
            [
                TraceStep(
                    stage="dispatch",
                    status="success",
                    summary="Home Assistant MCP",
                ),
                TraceStep(
                    stage="result",
                    status="success",
                    summary="工具返回成功",
                ),
            ]
        )
        return self._response(
            request_id=request_id,
            category=category,
            route="home_assistant_mcp",
            status=CommandStatus.SUCCESS,
            message=message,
            trace=trace,
            started_at=started_at,
            tool_call=ToolCallRecord(
                name=result.tool_name,
                arguments=arguments,
                result=result.content,
            ),
        )

    @staticmethod
    def _response(
        *,
        request_id: str,
        category: CommandCategory,
        route: str,
        status: CommandStatus,
        message: str,
        trace: list[TraceStep],
        started_at: float,
        tool_call: ToolCallRecord | None = None,
        error_code: str | None = None,
    ) -> CommandResponse:
        return CommandResponse(
            request_id=request_id,
            category=category,
            route=route,
            status=status,
            message=message,
            tool_call=tool_call,
            trace=trace,
            elapsed_ms=max(0, round((perf_counter() - started_at) * 1000)),
            error_code=error_code,
        )

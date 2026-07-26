from time import perf_counter
from typing import Protocol

from home_assist_agent.commands.models import (
    AnswerResult,
    CommandCategory,
    CommandResponse,
    CommandStatus,
    DevicePlanResult,
    RouteDecision,
    ToolCallRecord,
    ToolDefinition,
    TraceStep,
)
from home_assist_agent.devices.executor import (
    DeviceExecutionError,
    DeviceExecutor,
)
from home_assist_agent.errors import DependencyError
from home_assist_agent.ha.safety import SafetyViolation


class InstructionRouterProtocol(Protocol):
    async def route(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> RouteDecision: ...


class CodexServiceProtocol(Protocol):
    async def plan_device_control(
        self,
        command: str,
        intent_summary: str,
        tools: list[ToolDefinition],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> DevicePlanResult: ...

    async def answer(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AnswerResult: ...


class CommandOrchestrator:
    def __init__(
        self,
        router: InstructionRouterProtocol,
        codex: CodexServiceProtocol,
        devices: DeviceExecutor,
    ) -> None:
        self._router = router
        self._codex = codex
        self._devices = devices

    async def execute(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> CommandResponse:
        started_at = perf_counter()
        trace = [TraceStep(stage="input", status="success", summary="收到指令")]
        try:
            decision = await self._router.route(
                command,
                message_id,
                correlation_id,
                causation_id,
            )
        except DependencyError as error:
            trace.append(
                TraceStep(
                    stage="classify",
                    status="error",
                    summary=error.message,
                )
            )
            return self._response(
                message_id=message_id,
                category=CommandCategory.OTHER,
                route="codex",
                status=CommandStatus.ERROR,
                message=error.message,
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )

        trace.append(
            TraceStep(
                stage="classify",
                status="success",
                summary=self._classification_summary(decision.category),
            )
        )
        if decision.category == CommandCategory.DIRECT_IOT:
            return await self._execute_direct(
                decision,
                message_id,
                trace,
                started_at,
                correlation_id,
                causation_id,
            )
        if decision.category == CommandCategory.INDIRECT_IOT:
            return await self._execute_indirect(
                command,
                decision,
                message_id,
                trace,
                started_at,
                correlation_id,
                causation_id,
            )
        return await self._answer(
            command,
            message_id,
            trace,
            started_at,
            correlation_id,
            causation_id,
        )

    async def _execute_direct(
        self,
        decision: RouteDecision,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> CommandResponse:
        if decision.device_command is None:
            return self._response(
                message_id=message_id,
                category=CommandCategory.DIRECT_IOT,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message="指令路由没有返回完整的设备控制指令。",
                trace=trace,
                started_at=started_at,
                error_code="invalid_route_output",
            )
        try:
            tool_call = await self._devices.execute_direct(
                decision.device_command,
                message_id,
                correlation_id,
                causation_id,
            )
        except Exception as error:
            return self._device_error_response(
                error=error,
                message_id=message_id,
                category=CommandCategory.DIRECT_IOT,
                trace=trace,
                started_at=started_at,
            )
        return self._device_success_response(
            message_id=message_id,
            category=CommandCategory.DIRECT_IOT,
            message="Home Assistant 已处理该指令。",
            tool_call=tool_call,
            trace=trace,
            started_at=started_at,
        )

    async def _execute_indirect(
        self,
        command: str,
        decision: RouteDecision,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> CommandResponse:
        if not decision.intent_summary:
            return self._response(
                message_id=message_id,
                category=CommandCategory.INDIRECT_IOT,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message="指令路由没有返回设备控制意图。",
                trace=trace,
                started_at=started_at,
                error_code="invalid_route_output",
            )
        try:
            tools = await self._devices.list_safe_tools(
                message_id,
                correlation_id,
                causation_id,
            )
        except DependencyError as error:
            return self._device_error_response(
                error=error,
                message_id=message_id,
                category=CommandCategory.INDIRECT_IOT,
                trace=trace,
                started_at=started_at,
            )
        try:
            plan_result = await self._codex.plan_device_control(
                command,
                decision.intent_summary,
                tools,
                message_id,
                correlation_id,
                causation_id,
            )
        except DependencyError as error:
            trace.append(
                TraceStep(
                    stage="plan",
                    status="error",
                    summary=error.message,
                )
            )
            return self._response(
                message_id=message_id,
                category=CommandCategory.INDIRECT_IOT,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message=error.message,
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )
        trace.append(
            TraceStep(
                stage="plan",
                status="success",
                summary="Codex medium 生成设备计划",
            )
        )
        try:
            tool_call = await self._devices.execute_plan(
                plan_result.tool_plan,
                tools,
                message_id,
                correlation_id,
                causation_id,
            )
        except Exception as error:
            return self._device_error_response(
                error=error,
                message_id=message_id,
                category=CommandCategory.INDIRECT_IOT,
                trace=trace,
                started_at=started_at,
            )
        return self._device_success_response(
            message_id=message_id,
            category=CommandCategory.INDIRECT_IOT,
            message=plan_result.message,
            tool_call=tool_call,
            trace=trace,
            started_at=started_at,
        )

    async def _answer(
        self,
        command: str,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> CommandResponse:
        try:
            result = await self._codex.answer(
                command,
                message_id,
                correlation_id,
                causation_id,
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
                message_id=message_id,
                category=CommandCategory.OTHER,
                route="codex",
                status=CommandStatus.ERROR,
                message=error.message,
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )
        trace.append(
            TraceStep(
                stage="dispatch",
                status="success",
                summary="Codex high 普通回答",
            )
        )
        return self._response(
            message_id=message_id,
            category=CommandCategory.OTHER,
            route="codex",
            status=CommandStatus.SUCCESS,
            message=result.message,
            trace=trace,
            started_at=started_at,
        )

    def _device_error_response(
        self,
        *,
        error: Exception,
        message_id: str,
        category: CommandCategory,
        trace: list[TraceStep],
        started_at: float,
    ) -> CommandResponse:
        if isinstance(error, SafetyViolation):
            status = CommandStatus.BLOCKED
            code = error.code
            message = "该工具或目标不在 MVP 的安全执行范围内。"
        elif isinstance(error, (DependencyError, DeviceExecutionError)):
            status = CommandStatus.ERROR
            code = error.code
            message = error.message
        else:
            raise error
        trace.append(
            TraceStep(
                stage="dispatch",
                status=status,
                summary=message,
            )
        )
        return self._response(
            message_id=message_id,
            category=category,
            route="home_assistant_mcp",
            status=status,
            message=message,
            trace=trace,
            started_at=started_at,
            error_code=code,
        )

    def _device_success_response(
        self,
        *,
        message_id: str,
        category: CommandCategory,
        message: str,
        tool_call: ToolCallRecord,
        trace: list[TraceStep],
        started_at: float,
    ) -> CommandResponse:
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
            message_id=message_id,
            category=category,
            route="home_assistant_mcp",
            status=CommandStatus.SUCCESS,
            message=message,
            trace=trace,
            started_at=started_at,
            tool_call=tool_call,
        )

    @staticmethod
    def _classification_summary(category: CommandCategory) -> str:
        labels = {
            CommandCategory.DIRECT_IOT: "直接 IoT · Codex low",
            CommandCategory.INDIRECT_IOT: "模糊 IoT · Codex low",
            CommandCategory.OTHER: "其他指令 · Codex low",
        }
        return labels[category]

    @staticmethod
    def _response(
        *,
        message_id: str,
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
            message_id=message_id,
            request_id=message_id,
            category=category,
            route=route,
            status=status,
            message=message,
            tool_call=tool_call,
            trace=trace,
            elapsed_ms=max(0, round((perf_counter() - started_at) * 1000)),
            error_code=error_code,
        )

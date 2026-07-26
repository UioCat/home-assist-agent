from dataclasses import dataclass, field

import pytest

from home_assist_agent.commands.models import (
    AnswerResult,
    DeviceCommand,
    DevicePlanResult,
    RouteDecision,
    ToolDefinition,
    ToolExecutionResult,
    ToolPlan,
)
from home_assist_agent.commands.service import CommandOrchestrator
from home_assist_agent.devices.executor import DeviceExecutor
from home_assist_agent.errors import DependencyError


@dataclass
class FakeRouter:
    decision: RouteDecision | None = None
    error: DependencyError | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def route(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> RouteDecision:
        self.calls.append((command, message_id))
        if self.error:
            raise self.error
        assert self.decision is not None
        return self.decision


@dataclass
class FakeCodexService:
    plan_result: DevicePlanResult | None = None
    answer_result: AnswerResult | None = None
    plan_error: DependencyError | None = None
    answer_error: DependencyError | None = None
    plan_calls: list[dict[str, object]] = field(default_factory=list)
    answer_calls: list[tuple[str, str]] = field(default_factory=list)

    async def plan_device_control(
        self,
        command: str,
        intent_summary: str,
        tools: list[ToolDefinition],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> DevicePlanResult:
        self.plan_calls.append(
            {
                "command": command,
                "intent_summary": intent_summary,
                "tools": tools,
                "message_id": message_id,
            }
        )
        if self.plan_error:
            raise self.plan_error
        assert self.plan_result is not None
        return self.plan_result

    async def answer(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AnswerResult:
        self.answer_calls.append((command, message_id))
        if self.answer_error:
            raise self.answer_error
        assert self.answer_result is not None
        return self.answer_result


@dataclass
class FakeHaMcpClient:
    tools: list[ToolDefinition]
    list_error: DependencyError | None = None
    call_error: DependencyError | None = None
    list_calls: list[str] = field(default_factory=list)
    calls: list[tuple[str, dict[str, object], str]] = field(default_factory=list)

    async def list_tools(
        self,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[ToolDefinition]:
        self.list_calls.append(message_id)
        if self.list_error:
            raise self.list_error
        return self.tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ToolExecutionResult:
        self.calls.append((name, arguments, message_id))
        if self.call_error:
            raise self.call_error
        return ToolExecutionResult(tool_name=name, content="Done")


TURN_ON = ToolDefinition(
    name="assist.HassTurnOn",
    description="Turn on a device",
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
)
TURN_OFF = ToolDefinition(
    name="assist.HassTurnOff",
    description="Turn off a device",
    input_schema={"type": "object"},
)
LIGHT_SET = ToolDefinition(
    name="assist.HassLightSet",
    description="Set light brightness",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "brightness": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
            },
        },
        "required": ["name", "brightness"],
    },
)


def build_service(
    decision: RouteDecision,
    ha: FakeHaMcpClient,
    codex: FakeCodexService | None = None,
) -> tuple[CommandOrchestrator, FakeRouter, FakeCodexService]:
    router = FakeRouter(decision=decision)
    active_codex = codex or FakeCodexService()
    service = CommandOrchestrator(
        router=router,
        codex=active_codex,
        devices=DeviceExecutor(ha),
    )
    return service, router, active_codex


@pytest.mark.asyncio
async def test_direct_command_routes_once_and_executes_one_mcp_tool() -> None:
    decision = RouteDecision(
        category="direct_iot",
        device_command=DeviceCommand(
            action="turn_on",
            target="客厅灯",
        ),
    )
    ha = FakeHaMcpClient(tools=[TURN_ON])
    service, router, codex = build_service(decision, ha)

    response = await service.execute("打开客厅灯", "message-direct")

    assert response.category == "direct_iot"
    assert response.status == "success"
    assert response.route == "home_assistant_mcp"
    assert response.tool_call is not None
    assert response.tool_call.name == "assist.HassTurnOn"
    assert response.tool_call.arguments == {"name": "客厅灯"}
    assert router.calls == [("打开客厅灯", "message-direct")]
    assert codex.plan_calls == []
    assert codex.answer_calls == []
    assert ha.list_calls == ["message-direct"]
    assert ha.calls == [
        (
            "assist.HassTurnOn",
            {"name": "客厅灯"},
            "message-direct",
        )
    ]


@pytest.mark.asyncio
async def test_indirect_command_plans_then_executes_one_safe_tool() -> None:
    decision = RouteDecision(
        category="indirect_iot",
        intent_summary="调暗客厅灯",
    )
    codex = FakeCodexService(
        plan_result=DevicePlanResult(
            message="准备把客厅灯调暗。",
            tool_plan=ToolPlan(
                tool_name="HassLightSet",
                arguments={"name": "客厅灯", "brightness": 30},
            ),
        )
    )
    ha = FakeHaMcpClient(tools=[LIGHT_SET])
    service, _, _ = build_service(decision, ha, codex)

    response = await service.execute("客厅太暗了", "message-indirect")

    assert response.category == "indirect_iot"
    assert response.status == "success"
    assert response.tool_call is not None
    assert response.tool_call.name == "assist.HassLightSet"
    assert codex.plan_calls[0]["intent_summary"] == "调暗客厅灯"
    assert codex.answer_calls == []
    assert ha.list_calls == ["message-indirect"]
    assert ha.calls == [
        (
            "assist.HassLightSet",
            {"name": "客厅灯", "brightness": 30},
            "message-indirect",
        )
    ]


@pytest.mark.asyncio
async def test_other_command_answers_without_any_ha_request() -> None:
    decision = RouteDecision(category="other")
    codex = FakeCodexService(
        answer_result=AnswerResult(message="Home Assistant 是家庭自动化平台。")
    )
    ha = FakeHaMcpClient(
        tools=[],
        list_error=DependencyError("ha_not_configured", "HA 尚未配置"),
    )
    service, _, _ = build_service(decision, ha, codex)

    response = await service.execute(
        "解释什么是 Home Assistant",
        "message-other",
    )

    assert response.category == "other"
    assert response.status == "success"
    assert response.route == "codex"
    assert response.message == "Home Assistant 是家庭自动化平台。"
    assert codex.answer_calls == [("解释什么是 Home Assistant", "message-other")]
    assert codex.plan_calls == []
    assert ha.list_calls == []
    assert ha.calls == []


@pytest.mark.asyncio
async def test_unsafe_device_plan_is_blocked_before_mcp_call() -> None:
    decision = RouteDecision(
        category="indirect_iot",
        intent_summary="关闭前门锁",
    )
    codex = FakeCodexService(
        plan_result=DevicePlanResult(
            message="准备处理。",
            tool_plan=ToolPlan(
                tool_name="HassTurnOff",
                arguments={"name": "前门锁"},
            ),
        )
    )
    ha = FakeHaMcpClient(tools=[TURN_OFF])
    service, _, _ = build_service(decision, ha, codex)

    response = await service.execute("我准备出门了", "message-unsafe")

    assert response.category == "indirect_iot"
    assert response.status == "blocked"
    assert response.error_code == "unsafe_target"
    assert ha.calls == []


@pytest.mark.asyncio
async def test_indirect_command_reports_ha_error_before_planning() -> None:
    decision = RouteDecision(
        category="indirect_iot",
        intent_summary="调亮客厅",
    )
    codex = FakeCodexService()
    ha = FakeHaMcpClient(
        tools=[],
        list_error=DependencyError(
            "ha_unavailable",
            "无法连接 Home Assistant",
        ),
    )
    service, _, _ = build_service(decision, ha, codex)

    response = await service.execute("客厅太暗了", "message-ha-error")

    assert response.status == "error"
    assert response.error_code == "ha_unavailable"
    assert codex.plan_calls == []
    assert ha.calls == []


@pytest.mark.asyncio
async def test_mcp_failure_is_not_reported_as_success() -> None:
    decision = RouteDecision(
        category="direct_iot",
        device_command=DeviceCommand(
            action="turn_on",
            target="客厅灯",
        ),
    )
    ha = FakeHaMcpClient(
        tools=[TURN_ON],
        call_error=DependencyError("ha_unavailable", "工具调用失败"),
    )
    service, _, _ = build_service(decision, ha)

    response = await service.execute("打开客厅灯", "message-mcp-error")

    assert response.status == "error"
    assert response.error_code == "ha_unavailable"
    assert response.tool_call is None


@pytest.mark.asyncio
async def test_tool_arguments_must_match_live_mcp_schema() -> None:
    decision = RouteDecision(
        category="indirect_iot",
        intent_summary="调暗客厅灯",
    )
    codex = FakeCodexService(
        plan_result=DevicePlanResult(
            message="准备调灯。",
            tool_plan=ToolPlan(
                tool_name="HassLightSet",
                arguments={"name": "客厅灯", "brightness": "很暗"},
            ),
        )
    )
    ha = FakeHaMcpClient(tools=[LIGHT_SET])
    service, _, _ = build_service(decision, ha, codex)

    response = await service.execute("客厅太亮了", "message-schema")

    assert response.status == "error"
    assert response.error_code == "invalid_tool_arguments"
    assert ha.calls == []


@pytest.mark.asyncio
async def test_router_failure_does_not_access_ha() -> None:
    router = FakeRouter(error=DependencyError("codex_failed", "路由失败"))
    codex = FakeCodexService()
    ha = FakeHaMcpClient(tools=[TURN_ON])
    service = CommandOrchestrator(
        router=router,
        codex=codex,
        devices=DeviceExecutor(ha),
    )

    response = await service.execute("你好", "message-route-error")

    assert response.status == "error"
    assert response.error_code == "codex_failed"
    assert ha.list_calls == []
    assert ha.calls == []

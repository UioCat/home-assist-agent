from dataclasses import dataclass, field

import pytest

from home_assist_agent.commands.models import (
    CodexRouteResult,
    ToolDefinition,
    ToolExecutionResult,
    ToolPlan,
)
from home_assist_agent.commands.service import CommandService
from home_assist_agent.errors import DependencyError


@dataclass
class FakeCodexGateway:
    result: CodexRouteResult
    calls: list[dict[str, object]] = field(default_factory=list)

    async def route(
        self,
        command: str,
        reasoning: str,
        tools: list[ToolDefinition],
    ) -> CodexRouteResult:
        self.calls.append(
            {"command": command, "reasoning": reasoning, "tools": tools}
        )
        return self.result


@dataclass
class FakeHaMcpClient:
    tools: list[ToolDefinition]
    list_error: DependencyError | None = None
    call_error: DependencyError | None = None
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def list_tools(self) -> list[ToolDefinition]:
        if self.list_error:
            raise self.list_error
        return self.tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        self.calls.append((name, arguments))
        if self.call_error:
            raise self.call_error
        return ToolExecutionResult(tool_name=name, content="Done")


TURN_ON = ToolDefinition(
    name="assist.HassTurnOn",
    description="Turn on a device",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
)
LIGHT_SET = ToolDefinition(
    name="assist.HassLightSet",
    description="Set light brightness",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "brightness": {"type": "integer", "minimum": 0, "maximum": 100},
        },
    },
)


@pytest.mark.asyncio
async def test_direct_command_bypasses_codex_and_executes_one_mcp_tool() -> None:
    codex = FakeCodexGateway(
        CodexRouteResult(category="other", message="should not be used")
    )
    ha = FakeHaMcpClient(tools=[TURN_ON])
    service = CommandService(codex=codex, ha_mcp=ha)

    response = await service.execute("打开客厅灯", "high")

    assert response.category == "direct_iot"
    assert response.status == "success"
    assert response.route == "home_assistant_mcp"
    assert response.tool_call is not None
    assert response.tool_call.name == "assist.HassTurnOn"
    assert response.tool_call.arguments == {"name": "客厅灯"}
    assert codex.calls == []
    assert ha.calls == [("assist.HassTurnOn", {"name": "客厅灯"})]


@pytest.mark.asyncio
async def test_indirect_command_uses_codex_plan_then_executes_one_safe_tool() -> None:
    codex = FakeCodexGateway(
        CodexRouteResult(
            category="indirect_iot",
            message="准备把客厅灯调暗。",
            tool_plan=ToolPlan(
                tool_name="HassLightSet",
                arguments={"name": "客厅灯", "brightness": 30},
            ),
        )
    )
    ha = FakeHaMcpClient(tools=[LIGHT_SET])
    service = CommandService(codex=codex, ha_mcp=ha)

    response = await service.execute("客厅太暗了", "medium")

    assert response.category == "indirect_iot"
    assert response.status == "success"
    assert response.tool_call is not None
    assert response.tool_call.name == "assist.HassLightSet"
    assert ha.calls == [
        ("assist.HassLightSet", {"name": "客厅灯", "brightness": 30})
    ]
    assert len(codex.calls) == 1


@pytest.mark.asyncio
async def test_other_command_returns_codex_answer_without_calling_mcp_tool() -> None:
    codex = FakeCodexGateway(
        CodexRouteResult(category="other", message="Home Assistant 是家庭自动化平台。")
    )
    ha = FakeHaMcpClient(tools=[TURN_ON])
    service = CommandService(codex=codex, ha_mcp=ha)

    response = await service.execute("解释什么是 Home Assistant", "low")

    assert response.category == "other"
    assert response.status == "success"
    assert response.route == "codex"
    assert response.message == "Home Assistant 是家庭自动化平台。"
    assert response.tool_call is None
    assert ha.calls == []


@pytest.mark.asyncio
async def test_unsafe_codex_plan_is_blocked_before_mcp_call() -> None:
    codex = FakeCodexGateway(
        CodexRouteResult(
            category="indirect_iot",
            message="准备处理。",
            tool_plan=ToolPlan(
                tool_name="HassTurnOff",
                arguments={"name": "前门锁"},
            ),
        )
    )
    ha = FakeHaMcpClient(
        tools=[
            ToolDefinition(
                name="assist.HassTurnOff",
                description="Turn off a device",
                input_schema={"type": "object"},
            )
        ]
    )
    service = CommandService(codex=codex, ha_mcp=ha)

    response = await service.execute("我准备出门了", "high")

    assert response.category == "indirect_iot"
    assert response.status == "blocked"
    assert response.error_code == "unsafe_target"
    assert ha.calls == []


@pytest.mark.asyncio
async def test_other_codex_chat_still_works_when_ha_is_not_configured() -> None:
    codex = FakeCodexGateway(
        CodexRouteResult(category="other", message="你好，我可以帮助你。")
    )
    ha = FakeHaMcpClient(
        tools=[],
        list_error=DependencyError("ha_not_configured", "HA 尚未配置"),
    )
    service = CommandService(codex=codex, ha_mcp=ha)

    response = await service.execute("你好", "low")

    assert response.category == "other"
    assert response.status == "success"
    assert response.message == "你好，我可以帮助你。"
    assert codex.calls[0]["tools"] == []


@pytest.mark.asyncio
async def test_indirect_command_reports_ha_error_when_ha_is_unavailable() -> None:
    codex = FakeCodexGateway(
        CodexRouteResult(
            category="indirect_iot",
            message="准备开灯。",
            tool_plan=ToolPlan(
                tool_name="HassTurnOn",
                arguments={"name": "客厅灯"},
            ),
        )
    )
    ha = FakeHaMcpClient(
        tools=[],
        list_error=DependencyError("ha_unavailable", "无法连接 Home Assistant"),
    )
    service = CommandService(codex=codex, ha_mcp=ha)

    response = await service.execute("客厅太暗了", "medium")

    assert response.category == "indirect_iot"
    assert response.status == "error"
    assert response.error_code == "ha_unavailable"
    assert ha.calls == []


@pytest.mark.asyncio
async def test_mcp_failure_is_not_reported_as_success() -> None:
    codex = FakeCodexGateway(
        CodexRouteResult(category="other", message="should not be used")
    )
    ha = FakeHaMcpClient(
        tools=[TURN_ON],
        call_error=DependencyError("ha_unavailable", "工具调用失败"),
    )
    service = CommandService(codex=codex, ha_mcp=ha)

    response = await service.execute("打开客厅灯", "medium")

    assert response.category == "direct_iot"
    assert response.status == "error"
    assert response.error_code == "ha_unavailable"
    assert response.tool_call is None


@pytest.mark.asyncio
async def test_codex_tool_arguments_must_match_live_mcp_schema() -> None:
    codex = FakeCodexGateway(
        CodexRouteResult(
            category="indirect_iot",
            message="准备调灯。",
            tool_plan=ToolPlan(
                tool_name="HassLightSet",
                arguments={"name": "客厅灯", "brightness": "很暗"},
            ),
        )
    )
    ha = FakeHaMcpClient(tools=[LIGHT_SET])
    service = CommandService(codex=codex, ha_mcp=ha)

    response = await service.execute("客厅太亮了", "medium")

    assert response.category == "indirect_iot"
    assert response.status == "error"
    assert response.error_code == "invalid_tool_arguments"
    assert ha.calls == []

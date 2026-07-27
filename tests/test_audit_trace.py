from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import AsyncIterator

from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
import pytest

from home_assist_agent.audit.recorder import SQLiteAuditRecorder
from home_assist_agent.channels.message import MessageChannel
from home_assist_agent.codex.gateway import CodexGateway, ProcessResult
from home_assist_agent.commands.service import CommandOrchestrator
from home_assist_agent.devices.executor import DeviceExecutor
from home_assist_agent.errors import DependencyError
from home_assist_agent.ha.mcp_client import HomeAssistantMcpClient
from home_assist_agent.routing.service import InstructionRouter


@dataclass
class AuditRunner:
    outputs: list[str]
    calls: list[dict[str, object]] = field(default_factory=list)

    async def run(
        self,
        args: list[str],
        stdin: str,
        timeout_seconds: float,
    ) -> ProcessResult:
        self.calls.append(
            {
                "args": args,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        output_index = args.index("--output-last-message") + 1
        output = self.outputs.pop(0)
        Path(args[output_index]).write_text(output, encoding="utf-8")
        return ProcessResult(
            returncode=0,
            stdout=f"codex stdout {len(self.calls)}",
            stderr="",
        )


@dataclass
class AuditSession:
    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name="assist.HassLightSet",
                    description="Set brightness",
                    inputSchema={
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
            ]
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text="Done")],
            isError=False,
        )


def audit_session_factory(session: AuditSession):
    @asynccontextmanager
    async def factory(
        url: str,
        token: str,
        timeout_seconds: float,
    ) -> AsyncIterator[AuditSession]:
        yield session

    return factory


def build_message_channel(
    recorder: SQLiteAuditRecorder,
    runner: AuditRunner,
    *,
    ha_token: str | None = "top-secret",
) -> MessageChannel:
    codex = CodexGateway(
        runner=runner,
        audit=recorder,
    )
    ha_mcp = HomeAssistantMcpClient(
        url="http://homeassistant.local:8123/api/mcp",
        token=ha_token,
        session_factory=audit_session_factory(AuditSession()),
        audit=recorder,
    )
    orchestrator = CommandOrchestrator(
        router=InstructionRouter(codex),
        codex=codex,
        devices=DeviceExecutor(ha_mcp),
    )
    return MessageChannel(orchestrator, recorder)


@pytest.mark.asyncio
async def test_one_message_id_links_route_plan_and_external_exchanges(
    tmp_path: Path,
) -> None:
    recorder = SQLiteAuditRecorder(tmp_path / "audit.db")
    runner = AuditRunner(
        outputs=[
                (
                    '{"category":"indirect_iot","device_command":null,'
                    '"intent_summary":"调暗客厅灯",'
                    '"target_expression":"客厅灯",'
                    '"indirect_action":"set_brightness"}'
                ),
                (
                    '{"message":"准备调暗客厅灯。","tool_plan":{'
                    '"tool_name":"HassLightSet",'
                    '"arguments_json":"{\\"brightness\\":30}"}}'
                ),
        ]
    )
    channel = build_message_channel(recorder, runner)

    response = await channel.execute(
        "客厅太暗了",
        "high",
        message_id="message-chain",
    )
    events = await recorder.list_events("message-chain")

    assert response.message_id == "message-chain"
    assert response.request_id == "message-chain"
    assert response.status == "success"
    assert [event.sequence for event in events] == list(range(1, 11))
    assert [event.event_type for event in events] == [
        "user.request",
        "codex.request",
        "codex.response",
        "external.request",
        "external.response",
        "codex.request",
        "codex.response",
        "external.request",
        "external.response",
        "user.response",
    ]
    assert {event.message_id for event in events} == {"message-chain"}
    assert {event.correlation_id for event in events} == {"message-chain"}

    codex_requests = [event for event in events if event.event_type == "codex.request"]
    assert [event.payload["purpose"] for event in codex_requests] == [
        "route",
        "device_plan",
    ]
    assert [event.payload["reasoning"] for event in codex_requests] == [
        "low",
        "medium",
    ]
    assert "允许的 Home Assistant MCP 工具" not in (codex_requests[0].payload["prompt"])
    assert "assist.HassLightSet" in codex_requests[1].payload["prompt"]

    tool_request = next(
        event
        for event in events
        if event.event_type == "external.request"
        and event.payload["operation"] == "call_tool"
    )
    assert tool_request.payload["arguments"] == {
        "name": "客厅灯",
        "brightness": 30,
    }
    assert events[-1].payload["message"] == "准备调暗客厅灯。"
    assert "top-secret" not in json.dumps(
        [event.payload for event in events],
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_other_message_audit_never_contains_ha_exchange(
    tmp_path: Path,
) -> None:
    recorder = SQLiteAuditRecorder(tmp_path / "audit.db")
    runner = AuditRunner(
        outputs=[
            ('{"category":"other","device_command":null,"intent_summary":null}'),
            '{"message":"我可以帮助你。"}',
        ]
    )
    channel = build_message_channel(
        recorder,
        runner,
        ha_token=None,
    )

    response = await channel.execute(
        "介绍一下你能做什么",
        "medium",
        message_id="message-other",
    )
    events = await recorder.list_events("message-other")

    assert response.status == "success"
    assert response.message == "我可以帮助你。"
    assert [
        event.payload["purpose"]
        for event in events
        if event.event_type == "codex.request"
    ] == ["route", "answer"]
    assert all(event.service != "home_assistant_mcp" for event in events)


class FailingAuditRecorder:
    async def record(self, **kwargs) -> None:
        raise DependencyError("audit_unavailable", "审计写入失败")


@pytest.mark.asyncio
async def test_audit_failure_blocks_external_side_effect() -> None:
    entered_session = False

    @asynccontextmanager
    async def session_factory(
        url: str,
        token: str,
        timeout_seconds: float,
    ):
        nonlocal entered_session
        entered_session = True
        yield AuditSession()

    client = HomeAssistantMcpClient(
        url="http://homeassistant.local:8123/api/mcp",
        token="top-secret",
        session_factory=session_factory,
        audit=FailingAuditRecorder(),
    )

    with pytest.raises(DependencyError) as error:
        await client.call_tool(
            "assist.HassTurnOn",
            {"name": "客厅灯"},
            message_id="message-blocked",
        )

    assert error.value.code == "audit_unavailable"
    assert entered_session is False

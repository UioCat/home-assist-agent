from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
import pytest

from home_assist_agent.audit.recorder import SQLiteAuditRecorder
from home_assist_agent.channels.message import MessageChannel
from home_assist_agent.codex.gateway import CodexGateway, ProcessResult
from home_assist_agent.commands.service import CommandOrchestrator
from home_assist_agent.devices.executor import DeviceExecutor
from home_assist_agent.ha.catalog import HomeAssistantCatalogClient
from home_assist_agent.ha.mcp_client import HomeAssistantMcpClient
from home_assist_agent.resolution.candidates import CandidateBuilder
from home_assist_agent.resolution.models import ActorContext
from home_assist_agent.resolution.verifier import ResolutionVerifier
from home_assist_agent.routing.service import InstructionRouter
from home_assist_agent.terms.service import (
    DeterministicCorrectionResolver,
    TermLearningService,
)
from home_assist_agent.terms.store import SQLiteTermStore


@dataclass
class SequencedRunner:
    outputs: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def run(
        self,
        args: list[str],
        stdin: str,
        timeout_seconds: float,
    ) -> ProcessResult:
        self.calls.append({"args": args, "stdin": stdin})
        output_index = args.index("--output-last-message") + 1
        Path(args[output_index]).write_text(
            self.outputs.pop(0),
            encoding="utf-8",
        )
        return ProcessResult(
            returncode=0,
            stdout="codex stdout",
            stderr="",
        )


class RegistrySocket:
    def __init__(self) -> None:
        self.pending: list[dict[str, Any]] = []

    async def send(self, value: str) -> None:
        self.pending.append(json.loads(value))

    async def recv(self) -> str:
        request = self.pending.pop(0)
        results = {
            "config/entity_registry/list": [
                {
                    "entity_id": "light.bedside",
                    "name": None,
                    "original_name": "Bedside",
                    "aliases": ["床头灯"],
                    "device_id": "device-1",
                    "area_id": None,
                    "disabled_by": None,
                }
            ],
            "config/device_registry/list": [
                {
                    "id": "device-1",
                    "name": "Bedside device",
                    "name_by_user": "左床头灯",
                    "aliases": [],
                    "area_id": "bedroom",
                }
            ],
            "config/area_registry/list": [
                {
                    "area_id": "bedroom",
                    "name": "卧室",
                    "aliases": [],
                    "floor_id": None,
                }
            ],
        }
        return json.dumps(
            {
                "id": request["id"],
                "type": "result",
                "success": True,
                "result": results[request["type"]],
            }
        )


def registry_factory():
    @asynccontextmanager
    async def factory(
        url: str,
        token: str,
        timeout_seconds: float,
    ) -> AsyncIterator[RegistrySocket]:
        yield RegistrySocket()

    return factory


@dataclass
class McpSession:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name="assist.HassTurnOn",
                    description="Turn on",
                    inputSchema={
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                )
            ]
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        self.calls.append((name, arguments))
        return CallToolResult(
            content=[TextContent(type="text", text="Done")],
            isError=False,
        )


def mcp_factory(session: McpSession):
    @asynccontextmanager
    async def factory(
        url: str,
        token: str,
        timeout_seconds: float,
    ) -> AsyncIterator[McpSession]:
        yield session

    return factory


def ordered_subsequence(events: list[str], required: list[str]) -> bool:
    position = 0
    for event in events:
        if position < len(required) and event == required[position]:
            position += 1
    return position == len(required)


@pytest.mark.asyncio
async def test_success_chain_is_complete_and_raw_target_never_reaches_mcp(
    tmp_path: Path,
) -> None:
    token = "top-secret"
    audit = SQLiteAuditRecorder(tmp_path / "audit.db")

    async def states_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {token}"
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "entity_id": "light.bedside",
                    "state": "off",
                    "attributes": {"friendly_name": "左侧台灯"},
                }
            ],
        )

    catalog = HomeAssistantCatalogClient(
        base_url="http://ha.local:8123",
        token=token,
        http_transport=httpx.MockTransport(states_handler),
        websocket_factory=registry_factory(),
        audit=audit,
    )
    mcp_session = McpSession()
    mcp = HomeAssistantMcpClient(
        url="http://ha.local:8123/api/mcp",
        token=token,
        session_factory=mcp_factory(mcp_session),
        audit=audit,
    )
    runner = SequencedRunner(
        outputs=[
            json.dumps(
                {
                    "category": "direct_iot",
                    "device_command": {
                        "action": "turn_on",
                        "target_expression": "床头灯",
                        "parameters_json": "{}",
                    },
                    "intent_summary": None,
                    "target_expression": None,
                    "indirect_action": None,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "status": "selected",
                    "selected_candidate_id": "cand_01",
                    "confidence": 0.95,
                    "alternative_candidate_ids": [],
                    "reason": "别名匹配",
                },
                ensure_ascii=False,
            ),
        ]
    )
    codex = CodexGateway(runner=runner, audit=audit)
    terms = SQLiteTermStore(tmp_path / "terms.db", audit=audit)
    candidates = CandidateBuilder()
    verifier = ResolutionVerifier(
        catalog=catalog,
        audit=audit,
        confidence_threshold=0.80,
    )
    correction = DeterministicCorrectionResolver(
        catalog=catalog,
        term_store=terms,
        candidate_builder=candidates,
        codex=codex,
        verifier=verifier,
        audit=audit,
    )
    learning = TermLearningService(
        store=terms,
        audit=audit,
        correction_resolver=correction,
    )
    actor = ActorContext(home_id="home-1", person_id="person-1")
    orchestrator = CommandOrchestrator(
        router=InstructionRouter(codex),
        codex=codex,
        devices=DeviceExecutor(mcp),
        catalog=catalog,
        term_store=terms,
        candidate_builder=candidates,
        verifier=verifier,
        audit=audit,
        target_resolution_enabled=True,
        term_learning=learning,
        clock=lambda: datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    channel = MessageChannel(
        orchestrator=orchestrator,
        audit=audit,
        actor=actor,
    )

    response = await channel.execute(
        "打开床头灯",
        message_id="message-1",
    )

    assert response.message_id == response.request_id == "message-1"
    assert response.status == "success"
    assert mcp_session.calls == [
        ("assist.HassTurnOn", {"name": "light.bedside"})
    ]
    events = await audit.list_events("message-1")
    event_types = [event.event_type for event in events]
    assert ordered_subsequence(
        event_types,
        [
            "user.request",
            "codex.request",
            "codex.response",
            "external.request",
            "external.response",
            "target.candidates_generated",
            "codex.request",
            "codex.response",
            "external.request",
            "external.response",
            "target.verification_succeeded",
            "external.request",
            "external.response",
            "external.request",
            "external.response",
            "term.write.request",
            "term.provisional_created",
            "user.response",
        ],
    )
    assert {event.message_id for event in events} == {"message-1"}
    serialized = json.dumps(
        [event.payload for event in events],
        ensure_ascii=False,
    )
    assert token not in serialized
    assert '"name": "床头灯"' not in serialized
    target_prompt = runner.calls[1]["stdin"]
    assert "light.bedside" not in target_prompt

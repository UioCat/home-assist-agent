from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.errors import DependencyError
from home_assist_agent.ha.mcp_client import HomeAssistantMcpClient


@dataclass
class FakeSession:
    tools_result: ListToolsResult
    call_result: CallToolResult
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        return self.tools_result

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        self.calls.append((name, arguments))
        return self.call_result


def session_factory_for(session: FakeSession):
    @asynccontextmanager
    async def factory(
        url: str,
        token: str,
        timeout_seconds: float,
    ) -> AsyncIterator[FakeSession]:
        yield session

    return factory


def raising_session_factory(error: Exception):
    @asynccontextmanager
    async def factory(
        url: str,
        token: str,
        timeout_seconds: float,
    ) -> AsyncIterator[FakeSession]:
        raise error
        yield

    return factory


EMPTY_CALL_RESULT = CallToolResult(content=[], isError=False)


@pytest.mark.asyncio
async def test_live_mcp_tools_are_mapped_with_their_input_schema() -> None:
    session = FakeSession(
        tools_result=ListToolsResult(
            tools=[
                Tool(
                    name="assist.HassLightSet",
                    description="Set brightness",
                    inputSchema={
                        "type": "object",
                        "properties": {"brightness": {"type": "integer"}},
                    },
                )
            ]
        ),
        call_result=EMPTY_CALL_RESULT,
    )
    client = HomeAssistantMcpClient(
        url="http://homeassistant.local:8123/api/mcp",
        token="secret",
        session_factory=session_factory_for(session),
        audit=InMemoryAuditRecorder(),
    )

    tools = await client.list_tools("message-tools")

    assert len(tools) == 1
    assert tools[0].name == "assist.HassLightSet"
    assert tools[0].description == "Set brightness"
    assert tools[0].input_schema == {
        "type": "object",
        "properties": {"brightness": {"type": "integer"}},
    }


@pytest.mark.asyncio
async def test_successful_tool_call_returns_text_content() -> None:
    session = FakeSession(
        tools_result=ListToolsResult(tools=[]),
        call_result=CallToolResult(
            content=[TextContent(type="text", text="Done")],
            isError=False,
        ),
    )
    client = HomeAssistantMcpClient(
        url="http://homeassistant.local:8123/api/mcp",
        token="secret",
        session_factory=session_factory_for(session),
        audit=InMemoryAuditRecorder(),
    )

    result = await client.call_tool(
        "assist.HassTurnOn",
        {"name": "客厅灯"},
        "message-call",
    )

    assert result.tool_name == "assist.HassTurnOn"
    assert result.content == "Done"
    assert session.calls == [("assist.HassTurnOn", {"name": "客厅灯"})]


@pytest.mark.asyncio
async def test_mcp_tool_error_is_not_reported_as_success() -> None:
    session = FakeSession(
        tools_result=ListToolsResult(tools=[]),
        call_result=CallToolResult(
            content=[TextContent(type="text", text="Entity not found")],
            isError=True,
        ),
    )
    client = HomeAssistantMcpClient(
        url="http://homeassistant.local:8123/api/mcp",
        token="secret",
        session_factory=session_factory_for(session),
        audit=InMemoryAuditRecorder(),
    )

    with pytest.raises(DependencyError) as error:
        await client.call_tool(
            "assist.HassTurnOn",
            {"name": "不存在的灯"},
            "message-error",
        )

    assert error.value.code == "ha_tool_failed"


@pytest.mark.asyncio
async def test_missing_token_reports_not_configured_without_connecting() -> None:
    client = HomeAssistantMcpClient(
        url="http://homeassistant.local:8123/api/mcp",
        token=None,
        audit=InMemoryAuditRecorder(),
    )

    with pytest.raises(DependencyError) as error:
        await client.list_tools("message-unconfigured")

    assert error.value.code == "ha_not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(401, "ha_unauthorized"), (404, "ha_mcp_not_enabled")],
)
async def test_http_statuses_map_to_stable_error_codes(
    status_code: int,
    expected_code: str,
) -> None:
    request = httpx.Request("POST", "http://homeassistant.local:8123/api/mcp")
    response = httpx.Response(status_code, request=request)
    client = HomeAssistantMcpClient(
        url=str(request.url),
        token="secret",
        session_factory=raising_session_factory(
            httpx.HTTPStatusError(
                "failed",
                request=request,
                response=response,
            )
        ),
        audit=InMemoryAuditRecorder(),
    )

    with pytest.raises(DependencyError) as error:
        await client.list_tools("message-status")

    assert error.value.code == expected_code


@pytest.mark.asyncio
async def test_network_timeout_maps_to_ha_unavailable() -> None:
    client = HomeAssistantMcpClient(
        url="http://homeassistant.local:8123/api/mcp",
        token="secret",
        session_factory=raising_session_factory(httpx.ReadTimeout("timed out")),
        audit=InMemoryAuditRecorder(),
    )

    with pytest.raises(DependencyError) as error:
        await client.list_tools("message-timeout")

    assert error.value.code == "ha_unavailable"

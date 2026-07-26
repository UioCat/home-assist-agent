from dataclasses import dataclass

import pytest

from home_assist_agent.api.health import HealthService
from home_assist_agent.commands.models import ToolDefinition
from home_assist_agent.errors import DependencyError


@dataclass
class FakeCodexProbe:
    installed: bool
    authenticated: bool
    error_code: str | None = None

    async def check(self) -> tuple[bool, bool, str | None]:
        return self.installed, self.authenticated, self.error_code


@dataclass
class FakeHaMcpClient:
    tools: list[ToolDefinition]
    error: DependencyError | None = None

    async def list_tools(self) -> list[ToolDefinition]:
        if self.error:
            raise self.error
        return self.tools


@pytest.mark.asyncio
async def test_health_reports_connected_ha_and_live_tool_count() -> None:
    service = HealthService(
        codex_probe=FakeCodexProbe(True, True),
        ha_mcp=FakeHaMcpClient(
            tools=[
                ToolDefinition(name="assist.HassTurnOn"),
                ToolDefinition(name="assist.GetLiveContext"),
            ]
        ),
        ha_configured=True,
    )

    health = await service.snapshot()

    assert health.backend == "online"
    assert health.codex.installed is True
    assert health.codex.authenticated is True
    assert health.ha_mcp.configured is True
    assert health.ha_mcp.connected is True
    assert health.ha_mcp.tool_count == 2


@pytest.mark.asyncio
async def test_health_keeps_service_online_when_ha_is_unavailable() -> None:
    service = HealthService(
        codex_probe=FakeCodexProbe(True, True),
        ha_mcp=FakeHaMcpClient(
            tools=[],
            error=DependencyError("ha_unavailable", "HA 不可用"),
        ),
        ha_configured=True,
    )

    health = await service.snapshot()

    assert health.backend == "online"
    assert health.ha_mcp.configured is True
    assert health.ha_mcp.connected is False
    assert health.ha_mcp.error_code == "ha_unavailable"

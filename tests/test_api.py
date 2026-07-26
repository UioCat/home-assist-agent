from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from home_assist_agent.api.app import create_app
from home_assist_agent.api.models import (
    CodexHealth,
    HaMcpHealth,
    HealthResponse,
)
from home_assist_agent.commands.models import CommandResponse, TraceStep


@dataclass
class FakeCommandService:
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def execute(self, command: str, reasoning: str) -> CommandResponse:
        self.calls.append((command, reasoning))
        return CommandResponse(
            request_id="request-1",
            category="direct_iot",
            route="home_assistant_mcp",
            status="success",
            message="Home Assistant 已处理该指令。",
            trace=[
                TraceStep(
                    stage="classify",
                    status="success",
                    summary="直接 IoT",
                )
            ],
            elapsed_ms=25,
        )


class FakeHealthService:
    async def snapshot(self) -> HealthResponse:
        return HealthResponse(
            backend="online",
            codex=CodexHealth(installed=True, authenticated=True),
            ha_mcp=HaMcpHealth(
                configured=False,
                connected=False,
                tool_count=0,
                error_code="ha_not_configured",
            ),
        )


@pytest.fixture
def command_service() -> FakeCommandService:
    return FakeCommandService()


@pytest.fixture
def client(command_service: FakeCommandService) -> TestClient:
    app = create_app(
        command_service=command_service,
        health_service=FakeHealthService(),
    )
    return TestClient(app)


def test_command_endpoint_trims_input_and_uses_selected_reasoning(
    client: TestClient,
    command_service: FakeCommandService,
) -> None:
    response = client.post(
        "/api/commands",
        json={"command": "  打开客厅灯  ", "reasoning": "high"},
    )

    assert response.status_code == 200
    assert response.json()["category"] == "direct_iot"
    assert response.json()["status"] == "success"
    assert command_service.calls == [("打开客厅灯", "high")]


def test_command_endpoint_defaults_to_medium_reasoning(
    client: TestClient,
    command_service: FakeCommandService,
) -> None:
    response = client.post("/api/commands", json={"command": "你好"})

    assert response.status_code == 200
    assert command_service.calls == [("你好", "medium")]


@pytest.mark.parametrize(
    "payload",
    [
        {"command": ""},
        {"command": "   "},
        {"command": "a" * 1001},
        {"command": "你好", "reasoning": "ultra"},
    ],
)
def test_invalid_command_requests_are_rejected_before_dispatch(
    client: TestClient,
    command_service: FakeCommandService,
    payload: dict[str, str],
) -> None:
    response = client.post("/api/commands", json=payload)

    assert response.status_code == 422
    assert command_service.calls == []


def test_health_endpoint_exposes_status_without_credentials(
    client: TestClient,
) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "backend": "online",
        "codex": {
            "installed": True,
            "authenticated": True,
            "error_code": None,
        },
        "ha_mcp": {
            "configured": False,
            "connected": False,
            "tool_count": 0,
            "error_code": "ha_not_configured",
        },
    }
    assert "token" not in response.text.casefold()

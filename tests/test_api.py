from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from home_assist_agent.api.app import create_app
from home_assist_agent.api.models import (
    CodexHealth,
    HaMcpHealth,
    HealthResponse,
)
from home_assist_agent.audit.models import AuditEvent, AuditMessageSummary
from home_assist_agent.commands.models import CommandResponse, TraceStep
from home_assist_agent.events.models import EventRequest, EventResponse


@dataclass
class FakeCommandService:
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    async def execute(
        self,
        command: str,
        reasoning: str,
        message_id: str | None = None,
    ) -> CommandResponse:
        self.calls.append((command, reasoning, message_id))
        return CommandResponse(
            message_id=message_id or "request-1",
            request_id=message_id or "request-1",
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


@dataclass
class FakeEventService:
    calls: list[EventRequest] = field(default_factory=list)

    async def handle(self, event: EventRequest) -> EventResponse:
        self.calls.append(event)
        return EventResponse(
            message_id=event.message_id,
            request_id=event.message_id,
            status="observed",
            event_type=event.event_type,
        )


class FakePromotionWorker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")

    async def stop(self) -> None:
        self.calls.append("stop")


@dataclass
class FakeAuditQuery:
    messages: list[AuditMessageSummary] = field(default_factory=list)
    events: dict[str, list[AuditEvent]] = field(default_factory=dict)

    async def list_messages(
        self,
        limit: int = 50,
    ) -> list[AuditMessageSummary]:
        return self.messages[:limit]

    async def list_events(self, message_id: str) -> list[AuditEvent]:
        return self.events.get(message_id, [])


@pytest.fixture
def command_service() -> FakeCommandService:
    return FakeCommandService()


@pytest.fixture
def event_service() -> FakeEventService:
    return FakeEventService()


@pytest.fixture
def client(
    command_service: FakeCommandService,
    event_service: FakeEventService,
) -> TestClient:
    app = create_app(
        command_service=command_service,
        health_service=FakeHealthService(),
        audit_query=FakeAuditQuery(),
        event_service=event_service,
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
    assert command_service.calls == [("打开客厅灯", "high", None)]


def test_command_endpoint_defaults_to_medium_reasoning(
    client: TestClient,
    command_service: FakeCommandService,
) -> None:
    response = client.post("/api/commands", json={"command": "你好"})

    assert response.status_code == 200
    assert command_service.calls == [("你好", "medium", None)]


def test_command_endpoint_preserves_supplied_message_id(
    client: TestClient,
    command_service: FakeCommandService,
) -> None:
    response = client.post(
        "/api/commands",
        json={"command": "你好", "message_id": "platform-message-1"},
    )

    assert response.status_code == 200
    assert response.json()["message_id"] == "platform-message-1"
    assert response.json()["request_id"] == "platform-message-1"
    assert command_service.calls == [("你好", "medium", "platform-message-1")]


def test_command_response_forces_request_id_to_match_message_id() -> None:
    response = CommandResponse(
        message_id="message-canonical",
        request_id="legacy-mismatch",
        category="other",
        route="codex",
        status="success",
        message="你好",
    )

    assert response.message_id == "message-canonical"
    assert response.request_id == "message-canonical"


@pytest.mark.parametrize(
    "payload",
    [
        {"command": ""},
        {"command": "   "},
        {"command": "a" * 1001},
        {"command": "你好", "reasoning": "ultra"},
        {"command": "你好", "message_id": " "},
        {"command": "你好", "message_id": "a" * 129},
        {"command": "你好", "person_id": "forged-person"},
        {"command": "你好", "home_id": "other-home"},
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


def test_event_endpoint_normalizes_and_dispatches_event(
    client: TestClient,
    event_service: FakeEventService,
) -> None:
    response = client.post(
        "/api/events",
        json={
            "event_id": "ha-event-123",
            "event_type": "person.seated",
            "source": "home_assistant",
            "subject_id": "owner",
            "location": "study",
            "occurred_at": "2026-07-26T16:30:00+08:00",
            "attributes": {"confidence": 0.96},
            "correlation_id": "home-session-456",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "observed"
    assert response.json()["message_id"].startswith("event_")
    assert response.json()["request_id"] == response.json()["message_id"]
    assert event_service.calls[0].location == "study"


def test_event_endpoint_rejects_naive_timestamp(
    client: TestClient,
    event_service: FakeEventService,
) -> None:
    response = client.post(
        "/api/events",
        json={
            "event_id": "ha-event-123",
            "event_type": "person.seated",
            "source": "home_assistant",
            "subject_id": "owner",
            "occurred_at": "2026-07-26T16:30:00",
        },
    )

    assert response.status_code == 422
    assert event_service.calls == []


def test_audit_endpoints_list_messages_and_one_complete_trace() -> None:
    created_at = datetime(2026, 7, 26, 5, 30, tzinfo=timezone.utc)
    summary = AuditMessageSummary(
        message_id="message-1",
        command="打开客厅灯",
        response="Home Assistant 已处理该指令。",
        status="success",
        event_count=2,
        started_at=created_at,
        ended_at=created_at,
    )
    event = AuditEvent(
        event_id="event-1",
        message_id="message-1",
        sequence=1,
        event_type="user.request",
        service="web",
        payload={"command": "打开客厅灯"},
        status="success",
        created_at=created_at,
    )
    app = create_app(
        command_service=FakeCommandService(),
        health_service=FakeHealthService(),
        audit_query=FakeAuditQuery(
            messages=[summary],
            events={"message-1": [event]},
        ),
    )
    client = TestClient(app)

    messages_response = client.get("/api/audit")
    events_response = client.get("/api/audit/message-1")
    missing_response = client.get("/api/audit/missing")

    assert messages_response.status_code == 200
    assert messages_response.json()[0]["command"] == "打开客厅灯"
    assert messages_response.json()[0]["event_count"] == 2
    assert events_response.status_code == 200
    assert events_response.json()[0]["event_type"] == "user.request"
    assert missing_response.status_code == 404


def test_application_lifespan_starts_and_stops_term_worker() -> None:
    worker = FakePromotionWorker()
    app = create_app(
        command_service=FakeCommandService(),
        health_service=FakeHealthService(),
        audit_query=FakeAuditQuery(),
        promotion_worker=worker,
    )

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert worker.calls == ["start"]

    assert worker.calls == ["start", "stop"]

from dataclasses import dataclass, field

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.channels.message import MessageChannel
from home_assist_agent.commands.models import CommandResponse


@dataclass
class FakeOrchestrator:
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def execute(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> CommandResponse:
        self.calls.append((command, message_id))
        return CommandResponse(
            message_id=message_id,
            request_id=message_id,
            category="other",
            route="codex",
            status="success",
            message="你好",
        )


@pytest.mark.asyncio
async def test_message_channel_owns_ids_and_user_exchange_audit() -> None:
    audit = InMemoryAuditRecorder()
    orchestrator = FakeOrchestrator()
    channel = MessageChannel(orchestrator, audit)

    response = await channel.execute(
        "你好",
        reasoning="medium",
        message_id="message-channel",
    )
    events = await audit.list_events("message-channel")

    assert response.message_id == "message-channel"
    assert response.request_id == "message-channel"
    assert orchestrator.calls == [("你好", "message-channel")]
    assert [event.event_type for event in events] == [
        "user.request",
        "user.response",
    ]
    assert events[0].payload["command"] == "你好"
    assert events[0].payload["reasoning"] == "medium"
    assert events[0].payload["reasoning_policy"] == {
        "route": "low",
        "device_plan": "medium",
        "answer": "high",
    }
    assert events[1].payload["message"] == "你好"

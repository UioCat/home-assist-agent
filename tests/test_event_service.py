from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.automation.rules import NoopAutomationRuleEngine
from home_assist_agent.commands.models import CommandResponse
from home_assist_agent.context.store import SQLiteHouseholdContextStore
from home_assist_agent.events.models import (
    DerivedDeviceIntent,
    EventRequest,
    HouseholdContextEntry,
)
from home_assist_agent.events.service import EventService
from home_assist_agent.events.store import SQLiteEventReceiptStore
from home_assist_agent.resolution.models import ActorContext


def make_event(**overrides) -> EventRequest:
    payload = {
        "event_id": "ha-event-123",
        "event_type": "person.seated",
        "source": "home_assistant",
        "subject_id": "owner",
        "location": "study",
        "occurred_at": datetime(2026, 7, 26, 8, 30, tzinfo=timezone.utc),
        "attributes": {"confidence": 0.96},
        "correlation_id": "home-session-456",
    }
    payload.update(overrides)
    return EventRequest(**payload)


@dataclass
class FakeCommands:
    calls: list[
        tuple[str, str, str | None, str | None, ActorContext | None]
    ] = field(default_factory=list)

    async def execute(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        actor: ActorContext | None = None,
    ) -> CommandResponse:
        self.calls.append(
            (command, message_id, correlation_id, causation_id, actor)
        )
        return CommandResponse(
            message_id=message_id,
            request_id=message_id,
            category="direct_iot",
            route="home_assistant_mcp",
            status="success",
            message="已打开书房灯。",
        )


class MatchingRuleEngine:
    async def evaluate(
        self,
        event: EventRequest,
        context: HouseholdContextEntry,
    ) -> DerivedDeviceIntent:
        return DerivedDeviceIntent(
            rule_id="study-light",
            prompt="打开书房灯",
            source_message_id=event.message_id,
            correlation_id=event.active_correlation_id,
            causation_id=event.message_id,
        )


def build_event_service(
    tmp_path: Path,
    audit: InMemoryAuditRecorder,
    commands: FakeCommands,
    rules=None,
) -> EventService:
    database_path = tmp_path / "events.db"
    return EventService(
        receipts=SQLiteEventReceiptStore(database_path),
        context=SQLiteHouseholdContextStore(database_path),
        rules=rules or NoopAutomationRuleEngine(),
        commands=commands,
        audit=audit,
        actor=ActorContext(home_id="home-1", person_id="system"),
    )


def test_event_message_id_is_stable_and_scoped_by_source() -> None:
    first = make_event()
    duplicate = make_event()
    other_source = make_event(source="camera")

    assert first.message_id == duplicate.message_id
    assert first.message_id.startswith("event_")
    assert first.message_id != other_source.message_id


@pytest.mark.asyncio
async def test_unmatched_event_updates_context_without_commands(
    tmp_path: Path,
) -> None:
    audit = InMemoryAuditRecorder()
    commands = FakeCommands()
    service = build_event_service(tmp_path, audit, commands)
    event = make_event()

    response = await service.handle(event)
    events = await audit.list_events(event.message_id)

    assert response.status == "observed"
    assert response.message_id == event.message_id
    assert response.request_id == event.message_id
    assert commands.calls == []
    assert [item.event_type for item in events] == [
        "event.received",
        "context.update.request",
        "context.update.response",
        "automation.no_match",
        "event.response",
    ]
    assert {item.correlation_id for item in events} == {"home-session-456"}


@pytest.mark.asyncio
async def test_duplicate_event_does_not_update_context_or_execute_again(
    tmp_path: Path,
) -> None:
    audit = InMemoryAuditRecorder()
    commands = FakeCommands()
    service = build_event_service(tmp_path, audit, commands)
    event = make_event()

    first = await service.handle(event)
    second = await service.handle(event)
    events = await audit.list_events(event.message_id)

    assert first.status == "observed"
    assert second.status == "duplicate"
    assert second.message_id == first.message_id
    assert commands.calls == []
    assert sum(item.event_type == "context.update.request" for item in events) == 1
    assert any(item.event_type == "event.duplicate" for item in events)


@pytest.mark.asyncio
async def test_matching_rule_reuses_event_message_id_for_derived_command(
    tmp_path: Path,
) -> None:
    audit = InMemoryAuditRecorder()
    commands = FakeCommands()
    service = build_event_service(
        tmp_path,
        audit,
        commands,
        rules=MatchingRuleEngine(),
    )
    event = make_event(correlation_id=None)

    response = await service.handle(event)
    events = await audit.list_events(event.message_id)

    assert response.status == "triggered"
    assert response.rule_id == "study-light"
    assert commands.calls == [
        (
            "打开书房灯",
            event.message_id,
            event.message_id,
            event.message_id,
            ActorContext(home_id="home-1", person_id="system"),
        )
    ]
    assert any(item.event_type == "automation.matched" for item in events)
    assert any(item.event_type == "automation.result" for item in events)
    assert {item.correlation_id for item in events} == {event.message_id}


@pytest.mark.asyncio
async def test_derived_command_uses_rule_causation_not_parent_event_causation(
    tmp_path: Path,
) -> None:
    audit = InMemoryAuditRecorder()
    commands = FakeCommands()
    service = build_event_service(
        tmp_path,
        audit,
        commands,
        rules=MatchingRuleEngine(),
    )
    event = make_event(causation_id="event-entered-home")

    await service.handle(event)

    assert commands.calls == [
        (
            "打开书房灯",
            event.message_id,
            "home-session-456",
            event.message_id,
            ActorContext(home_id="home-1", person_id="system"),
        )
    ]

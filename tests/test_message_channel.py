from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.channels.message import MessageChannel
from home_assist_agent.commands.models import CommandResponse
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import ActorContext


@dataclass
class FakeOrchestrator:
    calls: list[tuple[str, str, ActorContext | None]] = field(default_factory=list)

    async def execute(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        actor: ActorContext | None = None,
    ) -> CommandResponse:
        self.calls.append((command, message_id, actor))
        return CommandResponse(
            message_id=message_id,
            request_id=message_id,
            category="other",
            route="codex",
            status="success",
            message="你好",
        )


class FailingOrchestrator:
    async def execute(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        actor: ActorContext | None = None,
    ) -> CommandResponse:
        raise DependencyError("codex_failed", "Codex 调用失败。")


@dataclass
class FakeConversationSession:
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    commits: list[tuple[str, str, str]] = field(default_factory=list)
    fail_commit: bool = False

    @asynccontextmanager
    async def conversation(
        self,
        *,
        conversation_id: str,
        thread_id: str | None,
        bind_thread: Callable[[str], Awaitable[None]],
    ) -> AsyncIterator[None]:
        self.calls.append((conversation_id, thread_id))
        if thread_id is None:
            await bind_thread("019c-thread-channel")
        yield

    async def commit_result(
        self,
        *,
        command: str,
        response: CommandResponse,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None:
        self.commits.append((command, response.message, message_id))
        if self.fail_commit:
            raise DependencyError(
                "codex_timeout",
                "本地 Codex 处理超时。",
            )


@pytest.mark.asyncio
async def test_message_channel_owns_ids_and_user_exchange_audit() -> None:
    audit = InMemoryAuditRecorder()
    orchestrator = FakeOrchestrator()
    actor = ActorContext(home_id="home-1", person_id="person-1")
    channel = MessageChannel(orchestrator, audit, actor=actor)

    response = await channel.execute(
        "你好",
        reasoning="medium",
        message_id="message-channel",
    )
    events = await audit.list_events("message-channel")

    assert response.message_id == "message-channel"
    assert response.request_id == "message-channel"
    assert orchestrator.calls == [("你好", "message-channel", actor)]
    assert [event.event_type for event in events] == [
        "user.request",
        "user.response",
    ]
    assert events[0].payload["command"] == "你好"
    assert events[0].payload["reasoning"] == "medium"
    assert events[0].payload["reasoning_policy"] == {
        "route": "low",
        "target_resolution": "medium",
        "device_plan": "medium",
        "answer": "high",
    }
    assert events[1].payload["message"] == "你好"


@pytest.mark.asyncio
async def test_message_channel_reuses_conversation_and_completed_receipt(
    tmp_path: Path,
) -> None:
    conversations = import_module("home_assist_agent.conversations.coordinator")
    store_module = import_module("home_assist_agent.conversations.store")
    audit = InMemoryAuditRecorder()
    session = FakeConversationSession()
    coordinator = conversations.ConversationCoordinator(
        store=store_module.SQLiteConversationStore(tmp_path / "conversations.db"),
        session=session,
        audit=audit,
    )
    orchestrator = FakeOrchestrator()
    actor = ActorContext(home_id="home-1", person_id="person-1")
    channel = MessageChannel(
        orchestrator,
        audit,
        actor=actor,
        conversations=coordinator,
    )

    first = await channel.execute(
        "你好",
        message_id="message-idempotent",
    )
    duplicate = await channel.execute(
        "你好",
        message_id="message-idempotent",
    )
    events = await audit.list_events("message-idempotent")

    assert first.conversation_id == duplicate.conversation_id
    assert first.conversation_id is not None
    assert len(orchestrator.calls) == 1
    assert len(session.calls) == 1
    assert session.commits == [
        ("你好", "你好", "message-idempotent")
    ]
    assert {event.conversation_id for event in events} == {
        first.conversation_id
    }


@pytest.mark.asyncio
async def test_commit_failure_returns_real_result_without_reexecution(
    tmp_path: Path,
) -> None:
    conversations = import_module("home_assist_agent.conversations.coordinator")
    store_module = import_module("home_assist_agent.conversations.store")
    audit = InMemoryAuditRecorder()
    session = FakeConversationSession(fail_commit=True)
    coordinator = conversations.ConversationCoordinator(
        store=store_module.SQLiteConversationStore(tmp_path / "conversations.db"),
        session=session,
        audit=audit,
    )
    orchestrator = FakeOrchestrator()
    channel = MessageChannel(
        orchestrator,
        audit,
        actor=ActorContext(home_id="home-1", person_id="person-1"),
        conversations=coordinator,
    )

    first = await channel.execute("打开灯", message_id="message-commit-failed")
    duplicate = await channel.execute("打开灯", message_id="message-commit-failed")
    events = await audit.list_events("message-commit-failed")

    assert first.status == "success"
    assert first.message == "你好"
    assert first.warnings == ["设备结果已返回，但会话上下文同步失败。"]
    assert duplicate == first
    assert len(orchestrator.calls) == 1
    assert len(session.commits) == 1
    assert any(
        event.event_type == "conversation.commit.response"
        and event.status == "error"
        and event.error_code == "codex_timeout"
        for event in events
    )


@pytest.mark.asyncio
async def test_channel_exposes_history_and_explicit_new_conversation(
    tmp_path: Path,
) -> None:
    conversations = import_module("home_assist_agent.conversations.coordinator")
    store_module = import_module("home_assist_agent.conversations.store")
    audit = InMemoryAuditRecorder()
    coordinator = conversations.ConversationCoordinator(
        store=store_module.SQLiteConversationStore(tmp_path / "conversations.db"),
        session=FakeConversationSession(),
        audit=audit,
    )
    channel = MessageChannel(
        FakeOrchestrator(),
        audit,
        actor=ActorContext(home_id="home-1", person_id="person-1"),
        conversations=coordinator,
    )

    response = await channel.execute("你好", message_id="message-history")
    current = await channel.current_conversation()
    created = await channel.create_conversation("message-new-conversation")
    replacement = await channel.current_conversation()
    create_events = await audit.list_events("message-new-conversation")

    assert current.conversation_id == response.conversation_id
    assert [item.message_id for item in current.messages] == ["message-history"]
    assert current.messages[0].response == response
    assert created.message_id == created.request_id == "message-new-conversation"
    assert created.conversation_id != current.conversation_id
    assert replacement.conversation_id == created.conversation_id
    assert replacement.messages == []
    assert [event.event_type for event in create_events] == [
        "user.request",
        "conversation.closed",
        "conversation.created",
        "user.response",
    ]


@pytest.mark.asyncio
async def test_failed_message_is_persisted_and_never_reprocessed(
    tmp_path: Path,
) -> None:
    conversations = import_module("home_assist_agent.conversations.coordinator")
    store_module = import_module("home_assist_agent.conversations.store")
    audit = InMemoryAuditRecorder()
    coordinator = conversations.ConversationCoordinator(
        store=store_module.SQLiteConversationStore(tmp_path / "conversations.db"),
        session=FakeConversationSession(),
        audit=audit,
    )
    channel = MessageChannel(
        FailingOrchestrator(),
        audit,
        actor=ActorContext(home_id="home-1", person_id="person-1"),
        conversations=coordinator,
    )

    with pytest.raises(DependencyError) as first_error:
        await channel.execute("你好", message_id="message-failed-receipt")
    current = await channel.current_conversation()
    with pytest.raises(DependencyError) as duplicate_error:
        await channel.execute("你好", message_id="message-failed-receipt")

    assert first_error.value.code == "codex_failed"
    assert current.messages[0].status == "failed"
    assert duplicate_error.value.code == "message_already_failed"

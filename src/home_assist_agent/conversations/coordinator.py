import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol

from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.commands.models import CommandResponse
from home_assist_agent.conversations.store import (
    ConversationThread,
    MessageReceipt,
    SQLiteConversationStore,
)
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import ActorContext


class ConversationSessionProtocol(Protocol):
    def conversation(
        self,
        *,
        conversation_id: str,
        thread_id: str | None,
        bind_thread: Callable[[str], Awaitable[None]],
    ): ...

    async def commit_result(
        self,
        *,
        command: str,
        response: CommandResponse,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ): ...


class ConversationCoordinator:
    def __init__(
        self,
        *,
        store: SQLiteConversationStore,
        session: ConversationSessionProtocol,
        audit: AuditRecorderProtocol,
    ) -> None:
        self._store = store
        self._session = session
        self._audit = audit
        self._locks: dict[str, asyncio.Lock] = {}

    async def resolve(
        self,
        *,
        actor: ActorContext,
        message_id: str,
        requested_conversation_id: str | None = None,
    ) -> ConversationThread:
        if requested_conversation_id is None:
            conversation = await self._store.resolve_active(
                actor.home_id,
                actor.person_id,
            )
        else:
            conversation = await self._store.get(requested_conversation_id)
            if (
                conversation is None
                or conversation.home_id != actor.home_id
                or conversation.person_id != actor.person_id
                or conversation.status not in {"creating", "active"}
            ):
                raise DependencyError(
                    "conversation_not_available",
                    "请求的会话不存在或不属于当前用户。",
                )
        await self._audit.record(
            message_id=message_id,
            conversation_id=conversation.conversation_id,
            event_type="conversation.resolve.request",
            service="conversation_store",
            payload={
                "home_id": actor.home_id,
                "person_id": actor.person_id,
                "requested_conversation_id": requested_conversation_id,
            },
            correlation_id=message_id,
        )
        await self._audit.record(
            message_id=message_id,
            conversation_id=conversation.conversation_id,
            event_type="conversation.resolve.response",
            service="conversation_store",
            payload={"status": conversation.status},
            correlation_id=message_id,
        )
        return conversation

    async def claim_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        channel: str,
        command: str,
    ) -> MessageReceipt:
        return await self._store.claim_message(
            message_id=message_id,
            conversation_id=conversation_id,
            channel=channel,
            command=command,
        )

    async def complete_message(
        self,
        message_id: str,
        response: dict[str, object],
    ) -> MessageReceipt:
        return await self._store.complete_message(message_id, response)

    async def list_messages(
        self,
        conversation_id: str,
    ) -> list[MessageReceipt]:
        return await self._store.list_messages(conversation_id)

    async def fail_message(self, message_id: str) -> MessageReceipt:
        return await self._store.fail_message(message_id)

    async def create_new(
        self,
        *,
        actor: ActorContext,
        message_id: str,
    ) -> ConversationThread:
        previous = await self._store.resolve_active(
            actor.home_id,
            actor.person_id,
        )
        await self._audit.record(
            message_id=message_id,
            conversation_id=previous.conversation_id,
            event_type="user.request",
            service="web",
            payload={"action": "new_conversation"},
            correlation_id=message_id,
        )
        created = await self._store.create_new(actor.home_id, actor.person_id)
        await self._audit.record(
            message_id=message_id,
            conversation_id=previous.conversation_id,
            event_type="conversation.closed",
            service="conversation_store",
            payload={"reason": "user_requested_new_conversation"},
            correlation_id=message_id,
        )
        await self._audit.record(
            message_id=message_id,
            conversation_id=created.conversation_id,
            event_type="conversation.created",
            service="conversation_store",
            payload={"status": created.status},
            correlation_id=message_id,
        )
        await self._audit.record(
            message_id=message_id,
            conversation_id=created.conversation_id,
            event_type="user.response",
            service="web",
            payload={"status": created.status},
            correlation_id=message_id,
        )
        return created

    async def commit_result(
        self,
        *,
        conversation_id: str,
        command: str,
        response: CommandResponse,
        message_id: str,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            conversation_id=conversation_id,
            event_type="conversation.commit.request",
            service="codex_cli",
            payload={"response_status": response.status.value},
            correlation_id=message_id,
        )
        try:
            result = await self._session.commit_result(
                command=command,
                response=response,
                message_id=message_id,
                correlation_id=message_id,
            )
        except DependencyError as error:
            await self._audit.record(
                message_id=message_id,
                conversation_id=conversation_id,
                event_type="conversation.commit.response",
                service="codex_cli",
                payload={"status": "failed", "error": error.message},
                status="error",
                error_code=error.code,
                correlation_id=message_id,
            )
            raise
        await self._audit.record(
            message_id=message_id,
            conversation_id=conversation_id,
            event_type="conversation.commit.response",
            service="codex_cli",
            payload={
                "status": "committed",
                "acknowledgement": getattr(result, "message", None),
            },
            correlation_id=message_id,
        )

    @asynccontextmanager
    async def turn(
        self,
        *,
        conversation: ConversationThread,
        message_id: str,
    ) -> AsyncIterator[None]:
        lock = self._locks.setdefault(conversation.conversation_id, asyncio.Lock())
        async with lock:
            current = await self._store.get(conversation.conversation_id)
            if current is None or current.status not in {"creating", "active"}:
                raise DependencyError(
                    "conversation_not_available",
                    "当前会话已关闭或不可用。",
                )

            async def bind_thread(thread_id: str) -> None:
                await self._audit.record(
                    message_id=message_id,
                    conversation_id=current.conversation_id,
                    event_type="conversation.thread_bind.request",
                    service="conversation_store",
                    payload={"codex_thread_id": thread_id},
                    correlation_id=message_id,
                )
                bound = await self._store.bind_thread(
                    current.conversation_id,
                    thread_id,
                )
                await self._audit.record(
                    message_id=message_id,
                    conversation_id=current.conversation_id,
                    event_type="conversation.thread_bind.response",
                    service="conversation_store",
                    payload={"status": bound.status},
                    correlation_id=message_id,
                )

            async with self._session.conversation(
                conversation_id=current.conversation_id,
                thread_id=current.codex_thread_id,
                bind_thread=bind_thread,
            ):
                yield

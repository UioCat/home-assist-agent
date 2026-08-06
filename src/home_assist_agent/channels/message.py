from typing import Protocol
from uuid import uuid4

from home_assist_agent.api.models import (
    ConversationCreated,
    ConversationMessage,
    ConversationView,
)
from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.commands.models import CommandResponse, ReasoningLevel
from home_assist_agent.conversations.coordinator import ConversationCoordinator
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import ActorContext


class CommandOrchestratorProtocol(Protocol):
    async def execute(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        actor: ActorContext | None = None,
    ) -> CommandResponse: ...


class MessageChannel:
    def __init__(
        self,
        orchestrator: CommandOrchestratorProtocol,
        audit: AuditRecorderProtocol,
        service: str = "web",
        actor: ActorContext | None = None,
        conversations: ConversationCoordinator | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._audit = audit
        self._service = service
        self._actor = actor
        self._conversations = conversations

    async def execute(
        self,
        command: str,
        reasoning: ReasoningLevel = "medium",
        message_id: str | None = None,
        conversation_id: str | None = None,
    ) -> CommandResponse:
        active_message_id = message_id or uuid4().hex
        if self._conversations is not None:
            if self._actor is None:
                raise DependencyError(
                    "actor_required",
                    "缺少可信调用身份。",
                )
            conversation = await self._conversations.resolve(
                actor=self._actor,
                message_id=active_message_id,
                requested_conversation_id=conversation_id,
            )
            receipt = await self._conversations.claim_message(
                message_id=active_message_id,
                conversation_id=conversation.conversation_id,
                channel=self._service,
                command=command,
            )
            if not receipt.is_new:
                if receipt.status == "completed" and receipt.response is not None:
                    return CommandResponse.model_validate(receipt.response)
                if receipt.status == "failed":
                    raise DependencyError(
                        "message_already_failed",
                        "该消息此前处理失败，不能自动重复执行。",
                    )
                raise DependencyError(
                    "message_in_progress",
                    "该消息正在处理或已经失败，不能重复执行。",
                )
            async with self._conversations.turn(
                conversation=conversation,
                message_id=active_message_id,
            ):
                return await self._execute_once(
                    command=command,
                    reasoning=reasoning,
                    message_id=active_message_id,
                    conversation_id=conversation.conversation_id,
                )
        return await self._execute_once(
            command=command,
            reasoning=reasoning,
            message_id=active_message_id,
            conversation_id=None,
        )

    async def _execute_once(
        self,
        *,
        command: str,
        reasoning: ReasoningLevel,
        message_id: str,
        conversation_id: str | None,
    ) -> CommandResponse:
        await self._audit.record(
            message_id=message_id,
            conversation_id=conversation_id,
            event_type="user.request",
            service=self._service,
            payload={
                "command": command,
                "reasoning": reasoning,
                "reasoning_policy": {
                    "route": "low",
                    "target_resolution": "medium",
                    "device_plan": "medium",
                    "answer": "high",
                },
            },
            correlation_id=message_id,
        )
        try:
            response = await self._orchestrator.execute(
                command,
                message_id,
                message_id,
                None,
                actor=self._actor,
            )
        except Exception as error:
            await self._audit.record(
                message_id=message_id,
                conversation_id=conversation_id,
                event_type="user.response",
                service=self._service,
                payload={"error": str(error)},
                status="error",
                error_code=getattr(
                    error,
                    "code",
                    error.__class__.__name__,
                ),
                correlation_id=message_id,
            )
            if self._conversations is not None:
                await self._conversations.fail_message(message_id)
            raise
        if conversation_id is not None:
            response = response.model_copy(
                update={"conversation_id": conversation_id}
            )
            if self._conversations is not None:
                try:
                    await self._conversations.commit_result(
                        conversation_id=conversation_id,
                        command=command,
                        response=response,
                        message_id=message_id,
                    )
                except DependencyError:
                    response = response.model_copy(
                        update={
                            "warnings": [
                                *response.warnings,
                                "设备结果已返回，但会话上下文同步失败。",
                            ]
                        }
                    )
        await self._audit.record(
            message_id=message_id,
            conversation_id=conversation_id,
            event_type="user.response",
            service=self._service,
            payload=response.model_dump(mode="json"),
            status=response.status.value,
            error_code=response.error_code,
            correlation_id=message_id,
        )
        if self._conversations is not None:
            await self._conversations.complete_message(
                message_id,
                response.model_dump(mode="json"),
            )
        return response

    async def current_conversation(self) -> ConversationView:
        if self._conversations is None or self._actor is None:
            raise DependencyError(
                "conversation_not_available",
                "会话功能尚未配置。",
            )
        conversation = await self._conversations.resolve(
            actor=self._actor,
            message_id=f"conversation_query_{uuid4().hex}",
        )
        receipts = await self._conversations.list_messages(
            conversation.conversation_id
        )
        return ConversationView(
            conversation_id=conversation.conversation_id,
            status=conversation.status,
            messages=[
                ConversationMessage(
                    message_id=receipt.message_id,
                    request_id=receipt.message_id,
                    channel=receipt.channel,
                    command=receipt.command,
                    status=receipt.status,
                    response=(
                        CommandResponse.model_validate(receipt.response)
                        if receipt.response is not None
                        else None
                    ),
                    created_at=receipt.created_at,
                    completed_at=receipt.completed_at,
                )
                for receipt in receipts
            ],
        )

    async def create_conversation(
        self,
        message_id: str | None = None,
    ) -> ConversationCreated:
        if self._conversations is None or self._actor is None:
            raise DependencyError(
                "conversation_not_available",
                "会话功能尚未配置。",
            )
        active_message_id = message_id or uuid4().hex
        conversation = await self._conversations.create_new(
            actor=self._actor,
            message_id=active_message_id,
        )
        return ConversationCreated(
            message_id=active_message_id,
            request_id=active_message_id,
            conversation_id=conversation.conversation_id,
            status=conversation.status,
        )

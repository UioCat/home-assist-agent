from typing import Protocol
from uuid import uuid4

from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.commands.models import CommandResponse, ReasoningLevel
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
    ) -> None:
        self._orchestrator = orchestrator
        self._audit = audit
        self._service = service
        self._actor = actor

    async def execute(
        self,
        command: str,
        reasoning: ReasoningLevel = "medium",
        message_id: str | None = None,
    ) -> CommandResponse:
        active_message_id = message_id or uuid4().hex
        await self._audit.record(
            message_id=active_message_id,
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
            correlation_id=active_message_id,
        )
        try:
            response = await self._orchestrator.execute(
                command,
                active_message_id,
                active_message_id,
                None,
                actor=self._actor,
            )
        except Exception as error:
            await self._audit.record(
                message_id=active_message_id,
                event_type="user.response",
                service=self._service,
                payload={"error": str(error)},
                status="error",
                error_code=getattr(
                    error,
                    "code",
                    error.__class__.__name__,
                ),
                correlation_id=active_message_id,
            )
            raise
        await self._audit.record(
            message_id=active_message_id,
            event_type="user.response",
            service=self._service,
            payload=response.model_dump(mode="json"),
            status=response.status.value,
            error_code=response.error_code,
            correlation_id=active_message_id,
        )
        return response

from typing import Protocol

from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.commands.models import CommandResponse
from home_assist_agent.events.models import (
    DerivedDeviceIntent,
    EventRequest,
    EventResponse,
    HouseholdContextEntry,
)


class EventReceiptStoreProtocol(Protocol):
    async def claim(
        self,
        *,
        source: str,
        event_id: str,
        message_id: str,
    ) -> bool: ...

    async def release(
        self,
        *,
        source: str,
        event_id: str,
    ) -> None: ...


class HouseholdContextStoreProtocol(Protocol):
    async def upsert(
        self,
        event: EventRequest,
        message_id: str,
    ) -> HouseholdContextEntry: ...


class AutomationRuleEngineProtocol(Protocol):
    async def evaluate(
        self,
        event: EventRequest,
        context: HouseholdContextEntry,
    ) -> DerivedDeviceIntent | None: ...


class CommandOrchestratorProtocol(Protocol):
    async def execute(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> CommandResponse: ...


class EventService:
    def __init__(
        self,
        *,
        receipts: EventReceiptStoreProtocol,
        context: HouseholdContextStoreProtocol,
        rules: AutomationRuleEngineProtocol,
        commands: CommandOrchestratorProtocol,
        audit: AuditRecorderProtocol,
    ) -> None:
        self._receipts = receipts
        self._context = context
        self._rules = rules
        self._commands = commands
        self._audit = audit

    async def handle(self, event: EventRequest) -> EventResponse:
        message_id = event.message_id
        correlation_id = event.active_correlation_id
        await self._audit.record(
            message_id=message_id,
            event_type="event.received",
            service="event_channel",
            payload=event.model_dump(mode="json"),
            correlation_id=correlation_id,
            causation_id=event.causation_id,
        )
        claimed = await self._receipts.claim(
            source=event.source,
            event_id=event.event_id,
            message_id=message_id,
        )
        if not claimed:
            response = EventResponse(
                message_id=message_id,
                request_id=message_id,
                status="duplicate",
                event_type=event.event_type,
            )
            await self._audit.record(
                message_id=message_id,
                event_type="event.duplicate",
                service="event_channel",
                payload={
                    "source": event.source,
                    "event_id": event.event_id,
                },
                correlation_id=correlation_id,
                causation_id=event.causation_id,
            )
            await self._record_response(
                response,
                correlation_id,
                event.causation_id,
            )
            return response

        may_have_external_side_effects = False
        try:
            context = await self._update_context(
                event,
                message_id,
                correlation_id,
            )
            intent = await self._rules.evaluate(event, context)
            if intent is None:
                await self._audit.record(
                    message_id=message_id,
                    event_type="automation.no_match",
                    service="automation",
                    payload={
                        "event_type": event.event_type,
                        "subject_id": event.subject_id,
                    },
                    correlation_id=correlation_id,
                    causation_id=event.causation_id,
                )
                response = EventResponse(
                    message_id=message_id,
                    request_id=message_id,
                    status="observed",
                    event_type=event.event_type,
                )
            else:
                derived_correlation_id = intent.correlation_id or correlation_id
                derived_causation_id = intent.causation_id
                await self._audit.record(
                    message_id=message_id,
                    event_type="automation.matched",
                    service="automation",
                    payload=intent.model_dump(mode="json"),
                    correlation_id=derived_correlation_id,
                    causation_id=derived_causation_id,
                )
                may_have_external_side_effects = True
                command_response = await self._commands.execute(
                    intent.prompt,
                    message_id,
                    derived_correlation_id,
                    derived_causation_id,
                )
                response = EventResponse(
                    message_id=message_id,
                    request_id=message_id,
                    status="triggered",
                    event_type=event.event_type,
                    rule_id=intent.rule_id,
                )
                await self._audit.record(
                    message_id=message_id,
                    event_type="automation.result",
                    service="automation",
                    payload=command_response.model_dump(mode="json"),
                    status=command_response.status.value,
                    error_code=command_response.error_code,
                    correlation_id=derived_correlation_id,
                    causation_id=derived_causation_id,
                )
            await self._record_response(
                response,
                correlation_id,
                event.causation_id,
            )
            return response
        except Exception as error:
            if not may_have_external_side_effects:
                await self._receipts.release(
                    source=event.source,
                    event_id=event.event_id,
                )
            await self._audit.record(
                message_id=message_id,
                event_type="event.response",
                service="event_channel",
                payload={"status": "error", "error": str(error)},
                status="error",
                error_code=getattr(
                    error,
                    "code",
                    error.__class__.__name__,
                ),
                correlation_id=correlation_id,
                causation_id=event.causation_id,
            )
            raise

    async def _update_context(
        self,
        event: EventRequest,
        message_id: str,
        correlation_id: str,
    ) -> HouseholdContextEntry:
        await self._audit.record(
            message_id=message_id,
            event_type="context.update.request",
            service="household_context",
            payload={
                "subject_id": event.subject_id,
                "event_type": event.event_type,
                "location": event.location,
                "attributes": event.attributes,
            },
            correlation_id=correlation_id,
            causation_id=event.causation_id,
        )
        try:
            context = await self._context.upsert(event, message_id)
        except Exception as error:
            await self._audit.record(
                message_id=message_id,
                event_type="context.update.response",
                service="household_context",
                payload={"error": str(error)},
                status="error",
                error_code=getattr(
                    error,
                    "code",
                    error.__class__.__name__,
                ),
                correlation_id=correlation_id,
                causation_id=event.causation_id,
            )
            raise
        await self._audit.record(
            message_id=message_id,
            event_type="context.update.response",
            service="household_context",
            payload=context.model_dump(mode="json"),
            correlation_id=correlation_id,
            causation_id=event.causation_id,
        )
        return context

    async def _record_response(
        self,
        response: EventResponse,
        correlation_id: str,
        causation_id: str | None,
    ) -> None:
        await self._audit.record(
            message_id=response.message_id,
            event_type="event.response",
            service="event_channel",
            payload=response.model_dump(mode="json"),
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

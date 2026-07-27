import asyncio
from datetime import UTC, datetime

from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.terms.models import PromotionSummary
from home_assist_agent.terms.store import SQLiteTermStore


class TermPromotionWorker:
    def __init__(
        self,
        *,
        store: SQLiteTermStore,
        audit: AuditRecorderProtocol,
        interval_seconds: float = 30,
    ) -> None:
        self._store = store
        self._audit = audit
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def run_once(self, now: datetime) -> PromotionSummary:
        due = await self._store.due_provisionals(now)
        checked: list[str] = []
        approved: list[str] = []
        skipped: list[str] = []
        for mapping in due:
            message_id = (
                f"term-promote-{mapping.mapping_id}-{mapping.revision}"
            )
            await self._audit.record(
                message_id=message_id,
                event_type="system.request",
                service="term_promotion_worker",
                payload={
                    "operation": "promote_term",
                    "mapping_id": mapping.mapping_id,
                    "revision": mapping.revision,
                },
                correlation_id=message_id,
                causation_id=mapping.source_message_id,
            )
            checked.append(mapping.mapping_id)
            await self._audit.record(
                message_id=message_id,
                event_type="term.promotion_checked",
                service="term_promotion_worker",
                payload={
                    "mapping_id": mapping.mapping_id,
                    "status": mapping.status.value,
                    "promote_at": mapping.promote_at,
                },
                correlation_id=message_id,
                causation_id=mapping.source_message_id,
            )
            try:
                result = await self._store.approve(
                    mapping.mapping_id,
                    message_id,
                    now=now,
                )
            except Exception as error:
                skipped.append(mapping.mapping_id)
                await self._audit.record(
                    message_id=message_id,
                    event_type="system.response",
                    service="term_promotion_worker",
                    payload={"error": str(error)},
                    status="error",
                    error_code=getattr(
                        error,
                        "code",
                        error.__class__.__name__,
                    ),
                    correlation_id=message_id,
                    causation_id=mapping.source_message_id,
                )
                continue
            approved.append(result.mapping_id)
            await self._audit.record(
                message_id=message_id,
                event_type="system.response",
                service="term_promotion_worker",
                payload={
                    "status": "approved",
                    "mapping_id": result.mapping_id,
                    "revision": result.revision,
                },
                correlation_id=message_id,
                causation_id=mapping.source_message_id,
            )
        return PromotionSummary(
            checked_mapping_ids=tuple(checked),
            approved_mapping_ids=tuple(approved),
            skipped_mapping_ids=tuple(skipped),
        )

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.run_once(datetime.now(UTC))
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self.run_once(datetime.now(UTC))

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
import re
from typing import Any, Protocol

from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.commands.models import DeviceExecutionBatch
from home_assist_agent.errors import DependencyError
from home_assist_agent.ha.catalog import HomeAssistantCatalogProvider
from home_assist_agent.resolution.candidates import CandidateBuilder
from home_assist_agent.resolution.models import (
    ActorContext,
    DeviceActionIntent,
    TargetCandidate,
    TargetResolutionDecision,
    VerifiedTarget,
)
from home_assist_agent.resolution.verifier import (
    ResolutionError,
    ResolutionVerifier,
)
from home_assist_agent.terms.models import (
    FeedbackOutcome,
    TermLearningOutcome,
    TermMapping,
    TermScope,
)
from home_assist_agent.terms.store import TermConflictError


class TermLearningStoreProtocol(Protocol):
    async def create_provisional(self, **kwargs: Any) -> TermMapping: ...

    async def latest_active_provisional(
        self,
        actor: ActorContext,
        now: datetime,
    ) -> TermMapping | None: ...

    async def reject(
        self,
        mapping_id: str,
        message_id: str,
        *,
        now: datetime,
    ) -> TermMapping: ...

    async def create_approved(self, **kwargs: Any) -> TermMapping: ...


class CorrectionResolverProtocol(Protocol):
    async def resolve_target_expression(
        self,
        *,
        expression: str,
        actor: ActorContext,
        message_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> VerifiedTarget: ...


class CorrectionCodexProtocol(Protocol):
    async def resolve_target(
        self,
        *,
        utterance: str,
        action_intent: DeviceActionIntent,
        candidates: list[TargetCandidate],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> TargetResolutionDecision: ...


class CorrectionTermStoreProtocol(Protocol):
    async def visible_terms(
        self,
        actor: ActorContext,
        now: datetime,
    ) -> Sequence[TermMapping]: ...


_CORRECTION = re.compile(
    r"^\s*(?:不是这个|不对)(?:\s*[，,。:：]?\s*(?:是|我说的是))?\s*(.*?)\s*$"
)
_EXPLICIT_TARGET = re.compile(r"^\s*我说的是\s*(.+?)\s*$")


class DeterministicCorrectionResolver:
    def __init__(
        self,
        *,
        catalog: HomeAssistantCatalogProvider,
        term_store: CorrectionTermStoreProtocol,
        candidate_builder: CandidateBuilder,
        codex: CorrectionCodexProtocol,
        verifier: ResolutionVerifier,
        audit: AuditRecorderProtocol,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._catalog = catalog
        self._term_store = term_store
        self._candidate_builder = candidate_builder
        self._codex = codex
        self._verifier = verifier
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))

    async def resolve_target_expression(
        self,
        *,
        expression: str,
        actor: ActorContext,
        message_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> VerifiedTarget:
        intent = DeviceActionIntent(
            action="turn_on",
            target_expression=expression,
        )
        for resolution_round in range(2):
            catalog = await self._catalog.snapshot(
                actor,
                message_id,
                correlation_id,
                causation_id,
            )
            terms = await self._term_store.visible_terms(
                actor,
                self._clock(),
            )
            candidates = self._candidate_builder.build(
                intent=intent,
                actor=actor,
                catalog=catalog,
                terms=terms,
            )
            await self._audit.record(
                message_id=message_id,
                event_type="target.candidates_generated",
                service="term_correction",
                payload={
                    "round": resolution_round + 1,
                    "target_expression": expression,
                    "catalog_version": catalog.catalog_version,
                    "candidates": [
                        candidate.model_dump(mode="json")
                        for candidate in candidates
                    ],
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            if not candidates:
                raise ResolutionError(
                    "target_not_found",
                    "纠正表达没有匹配到设备候选。",
                )
            decision = await self._codex.resolve_target(
                utterance=expression,
                action_intent=intent,
                candidates=candidates,
                message_id=message_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            try:
                return await self._verifier.verify(
                    decision=decision,
                    candidates=candidates,
                    actor=actor,
                    intent=intent,
                    message_id=message_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
            except ResolutionError as error:
                if error.retryable and resolution_round == 0:
                    continue
                raise
        raise AssertionError("correction resolution loop must return")


class TermLearningService:
    def __init__(
        self,
        *,
        store: TermLearningStoreProtocol,
        audit: AuditRecorderProtocol,
        correction_resolver: CorrectionResolverProtocol | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._correction_resolver = correction_resolver

    async def record_success(
        self,
        *,
        actor: ActorContext,
        expression: str,
        target: VerifiedTarget,
        execution: DeviceExecutionBatch,
        source_message_id: str,
        now: datetime,
    ) -> TermLearningOutcome:
        if (
            not expression.strip()
            or not execution.fully_succeeded
            or tuple(execution.completed) != target.entity_ids
        ):
            await self._audit.record(
                message_id=source_message_id,
                event_type="term.learning_skipped",
                service="term_learning",
                payload={
                    "reason": "execution_not_fully_successful",
                    "candidate_id": target.candidate_id,
                },
            )
            return TermLearningOutcome()
        try:
            mapping = await self._store.create_provisional(
                actor=actor,
                display_term=expression,
                entity_ids=target.entity_ids,
                source_message_id=source_message_id,
                source_candidate_id=target.candidate_id,
                catalog_version=target.catalog_version,
                now=now,
                evidence={
                    "execution_complete": True,
                    "entity_count": len(target.entity_ids),
                },
            )
        except TermConflictError as error:
            await self._audit.record(
                message_id=source_message_id,
                event_type="term.learning_conflict",
                service="term_learning",
                payload={
                    "mapping_id": error.existing.mapping_id,
                    "existing_target_entity_ids": (
                        error.existing.target_entity_ids
                    ),
                    "new_target_entity_ids": target.entity_ids,
                },
                status="blocked",
                error_code="term_conflict",
            )
            return TermLearningOutcome(
                mapping=error.existing,
                warnings=("term_conflict",),
            )
        except DependencyError as error:
            await self._audit.record(
                message_id=source_message_id,
                event_type="term.learning_failed",
                service="term_learning",
                payload={"error": error.message},
                status="error",
                error_code=error.code,
            )
            return TermLearningOutcome(
                warnings=("term_learning_unavailable",),
            )
        return TermLearningOutcome(
            learned=True,
            mapping=mapping,
            prompt_user=False,
        )

    async def handle_feedback(
        self,
        *,
        actor: ActorContext,
        text: str,
        message_id: str,
        now: datetime,
    ) -> FeedbackOutcome:
        replacement = self._correction_expression(text)
        if replacement is False:
            return FeedbackOutcome(handled=False)
        active = await self._store.latest_active_provisional(actor, now)
        if active is None:
            return FeedbackOutcome(handled=False)

        await self._store.reject(
            active.mapping_id,
            message_id,
            now=now,
        )
        if replacement is None:
            return FeedbackOutcome(
                handled=True,
                message="已撤销刚才学习的个人称呼。",
            )
        if self._correction_resolver is None:
            return FeedbackOutcome(
                handled=True,
                message="已撤销原称呼，但暂时无法解析新目标。",
                replacement_expression=replacement,
                warnings=("correction_resolution_unavailable",),
            )
        try:
            resolved = await self._correction_resolver.resolve_target_expression(
                expression=replacement,
                actor=actor,
                message_id=message_id,
                correlation_id=active.source_message_id,
                causation_id=active.source_message_id,
            )
            mapping = await self._store.create_approved(
                actor=actor,
                scope=TermScope.PERSON,
                display_term=active.display_term,
                entity_ids=resolved.entity_ids,
                source_message_id=message_id,
                source_candidate_id=resolved.candidate_id,
                catalog_version=resolved.catalog_version,
                now=now,
                evidence={"explicit_correction": True},
                supersedes_mapping_id=active.mapping_id,
            )
        except (DependencyError, ResolutionError, TermConflictError) as error:
            code = getattr(error, "code", "term_conflict")
            await self._audit.record(
                message_id=message_id,
                event_type="term.correction_failed",
                service="term_learning",
                payload={
                    "mapping_id": active.mapping_id,
                    "replacement_expression": replacement,
                    "error": str(error),
                },
                status="error",
                error_code=code,
                correlation_id=active.source_message_id,
                causation_id=active.source_message_id,
            )
            return FeedbackOutcome(
                handled=True,
                message="已撤销原称呼，但新目标尚未保存。",
                replacement_expression=replacement,
                warnings=("term_correction_unavailable",),
            )
        return FeedbackOutcome(
            handled=True,
            message="已按你的纠正更新个人称呼。",
            mapping=mapping,
            replacement_expression=replacement,
        )

    @staticmethod
    def _correction_expression(text: str) -> str | None | bool:
        explicit = _EXPLICIT_TARGET.match(text)
        if explicit:
            return explicit.group(1).strip()
        matched = _CORRECTION.match(text)
        if not matched:
            return False
        replacement = matched.group(1).strip()
        return replacement or None

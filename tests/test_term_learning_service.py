from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.commands.models import (
    DeviceExecutionBatch,
    DeviceExecutionFailure,
    ToolCallRecord,
)
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import ActorContext, VerifiedTarget
from home_assist_agent.resolution.candidates import CandidateBuilder
from home_assist_agent.resolution.models import (
    CatalogSnapshot,
    HaEntitySnapshot,
    TargetResolutionDecision,
)
from home_assist_agent.resolution.verifier import ResolutionVerifier
from home_assist_agent.resolution.verifier import ResolutionError
from home_assist_agent.terms.models import TermScope, TermStatus
from home_assist_agent.terms.service import (
    DeterministicCorrectionResolver,
    TermLearningService,
)
from home_assist_agent.terms.store import SQLiteTermStore


NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
ACTOR = ActorContext(home_id="home-1", person_id="person-1")


def verified(entity_id: str) -> VerifiedTarget:
    return VerifiedTarget(
        home_id="home-1",
        candidate_id="cand_01",
        entity_ids=(entity_id,),
        catalog_version="catalog-v1",
        action="turn_on",
    )


def successful_batch(entity_id: str) -> DeviceExecutionBatch:
    return DeviceExecutionBatch(
        completed=(entity_id,),
        tool_calls=(
            ToolCallRecord(
                name="assist.HassTurnOn",
                arguments={"name": entity_id},
                result="Done",
            ),
        ),
    )


def partial_batch() -> DeviceExecutionBatch:
    return DeviceExecutionBatch(
        completed=("light.left",),
        failed=(
            DeviceExecutionFailure(
                entity_id="light.right",
                error_code="ha_tool_failed",
                message="失败",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_successful_execution_creates_personal_provisional_without_prompt(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    service = TermLearningService(
        store=store,
        audit=InMemoryAuditRecorder(),
    )

    outcome = await service.record_success(
        actor=ACTOR,
        expression="床头灯",
        target=verified("light.bedside"),
        execution=successful_batch("light.bedside"),
        source_message_id="message-1",
        now=NOW,
    )

    assert outcome.mapping is not None
    assert outcome.mapping.status == TermStatus.PROVISIONAL
    assert outcome.mapping.scope == TermScope.PERSON
    assert outcome.mapping.promote_at == NOW + timedelta(seconds=600)
    assert outcome.prompt_user is False
    assert outcome.warnings == ()
    serialized = outcome.mapping.model_dump_json()
    assert "turn_on" not in serialized
    assert "brightness" not in serialized


@pytest.mark.asyncio
async def test_partial_execution_never_learns_term(tmp_path: Path) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    service = TermLearningService(
        store=store,
        audit=InMemoryAuditRecorder(),
    )

    outcome = await service.record_success(
        actor=ACTOR,
        expression="床头灯",
        target=verified("light.left"),
        execution=partial_batch(),
        source_message_id="message-partial",
        now=NOW,
    )

    assert outcome.mapping is None
    assert outcome.learned is False
    assert await store.visible_terms(ACTOR, NOW) == []


@pytest.mark.asyncio
async def test_same_approved_mapping_is_not_downgraded(tmp_path: Path) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    approved = await store.create_approved(
        actor=ACTOR,
        scope=TermScope.PERSON,
        display_term="床头灯",
        entity_ids=("light.bedside",),
        source_message_id="message-approved",
        source_candidate_id="cand-old",
        catalog_version="catalog-v1",
        now=NOW,
    )
    service = TermLearningService(
        store=store,
        audit=InMemoryAuditRecorder(),
    )

    outcome = await service.record_success(
        actor=ACTOR,
        expression="床头灯",
        target=verified("light.bedside"),
        execution=successful_batch("light.bedside"),
        source_message_id="message-reuse",
        now=NOW + timedelta(minutes=1),
    )

    assert outcome.mapping is not None
    assert outcome.mapping.mapping_id == approved.mapping_id
    assert outcome.mapping.status == TermStatus.APPROVED


@pytest.mark.asyncio
async def test_conflicting_approved_mapping_is_not_overwritten(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    original = await store.create_approved(
        actor=ACTOR,
        scope=TermScope.PERSON,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-approved",
        source_candidate_id="cand-left",
        catalog_version="catalog-v1",
        now=NOW,
    )
    service = TermLearningService(
        store=store,
        audit=InMemoryAuditRecorder(),
    )

    outcome = await service.record_success(
        actor=ACTOR,
        expression="床头灯",
        target=verified("light.right"),
        execution=successful_batch("light.right"),
        source_message_id="message-conflict",
        now=NOW + timedelta(minutes=1),
    )

    assert outcome.learned is False
    assert outcome.warnings == ("term_conflict",)
    visible = await store.visible_terms(ACTOR, NOW + timedelta(minutes=1))
    assert visible[0].mapping_id == original.mapping_id
    assert visible[0].target_entity_ids == ("light.left",)


@pytest.mark.asyncio
async def test_not_this_rejects_active_provisional_without_device_action(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    mapping = await store.create_provisional(
        actor=ACTOR,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-original",
        source_candidate_id="cand-left",
        catalog_version="catalog-v1",
        now=NOW,
    )
    service = TermLearningService(
        store=store,
        audit=InMemoryAuditRecorder(),
    )

    outcome = await service.handle_feedback(
        actor=ACTOR,
        text="不是这个",
        message_id="message-correction",
        now=NOW + timedelta(minutes=1),
    )

    assert outcome.handled is True
    assert outcome.replacement_expression is None
    assert await store.revision_count(mapping.mapping_id) == 2
    assert await store.visible_terms(ACTOR, NOW + timedelta(minutes=1)) == []


class RecordingCorrectionResolver:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def resolve_target_expression(
        self,
        *,
        expression: str,
        actor: ActorContext,
        message_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> VerifiedTarget:
        self.calls.append(
            {
                "expression": expression,
                "message_id": message_id,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            }
        )
        return VerifiedTarget(
            home_id=actor.home_id,
            candidate_id="cand_02",
            entity_ids=("light.desk",),
            catalog_version="catalog-v2",
            action="turn_on",
        )


@pytest.mark.asyncio
async def test_explicit_replacement_is_approved_without_reexecution(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    original = await store.create_provisional(
        actor=ACTOR,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-original",
        source_candidate_id="cand-left",
        catalog_version="catalog-v1",
        now=NOW,
    )
    resolver = RecordingCorrectionResolver()
    service = TermLearningService(
        store=store,
        audit=InMemoryAuditRecorder(),
        correction_resolver=resolver,
    )

    outcome = await service.handle_feedback(
        actor=ACTOR,
        text="不是这个，是书桌灯",
        message_id="message-correction",
        now=NOW + timedelta(minutes=1),
    )

    assert outcome.handled is True
    assert outcome.mapping is not None
    assert outcome.mapping.status == TermStatus.APPROVED
    assert outcome.mapping.display_term == "床头灯"
    assert outcome.mapping.target_entity_ids == ("light.desk",)
    assert outcome.mapping.supersedes_mapping_id == original.mapping_id
    assert resolver.calls == [
        {
            "expression": "书桌灯",
            "message_id": "message-correction",
            "correlation_id": "message-original",
            "causation_id": "message-original",
        }
    ]


class CorrectionCatalog:
    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self.snapshot_value = snapshot
        self.calls = 0

    async def snapshot(
        self,
        actor: ActorContext,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> CatalogSnapshot:
        self.calls += 1
        return self.snapshot_value


class CorrectionCodex:
    def __init__(self) -> None:
        self.candidate_ids: list[str] = []

    async def resolve_target(
        self,
        *,
        utterance: str,
        action_intent,
        candidates,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> TargetResolutionDecision:
        self.candidate_ids = [item.candidate_id for item in candidates]
        return TargetResolutionDecision(
            status="selected",
            selected_candidate_id=candidates[0].candidate_id,
            confidence=0.95,
            alternative_candidate_ids=(),
            reason="别名匹配",
        )


@pytest.mark.asyncio
async def test_correction_resolver_uses_candidate_codex_verifier_without_executor(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    await store.create_provisional(
        actor=ACTOR,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-original",
        source_candidate_id="cand-left",
        catalog_version="catalog-v1",
        now=NOW,
    )
    entity = HaEntitySnapshot(
        home_id="home-1",
        entity_id="light.desk",
        domain="light",
        friendly_name="书桌灯",
        aliases=("书桌灯",),
        state="off",
        capabilities=frozenset({"turn_on", "turn_off"}),
        available=True,
    )
    snapshot = CatalogSnapshot(
        home_id="home-1",
        catalog_version="catalog-v2",
        observed_at=NOW,
        entities=(entity,),
    )
    catalog = CorrectionCatalog(snapshot)
    codex = CorrectionCodex()
    audit = InMemoryAuditRecorder()
    resolver = DeterministicCorrectionResolver(
        catalog=catalog,
        term_store=store,
        candidate_builder=CandidateBuilder(),
        codex=codex,
        verifier=ResolutionVerifier(
            catalog=catalog,
            audit=audit,
            confidence_threshold=0.80,
        ),
        audit=audit,
        clock=lambda: NOW + timedelta(minutes=1),
    )
    service = TermLearningService(
        store=store,
        audit=audit,
        correction_resolver=resolver,
    )

    outcome = await service.handle_feedback(
        actor=ACTOR,
        text="不是这个，是书桌灯",
        message_id="message-correction",
        now=NOW + timedelta(minutes=1),
    )

    assert outcome.mapping is not None
    assert outcome.mapping.target_entity_ids == ("light.desk",)
    assert codex.candidate_ids == ["cand_01"]
    assert catalog.calls == 2


class FailingTermStore:
    async def create_provisional(self, **kwargs):
        raise DependencyError("term_store_unavailable", "术语存储写入失败。")


class NoMatchCorrectionResolver:
    async def resolve_target_expression(self, **kwargs):
        raise ResolutionError("target_not_found", "没有匹配到设备。")


@pytest.mark.asyncio
async def test_unresolved_replacement_returns_warning_after_rejection(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    await store.create_provisional(
        actor=ACTOR,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-original",
        source_candidate_id="cand-left",
        catalog_version="catalog-v1",
        now=NOW,
    )
    service = TermLearningService(
        store=store,
        audit=InMemoryAuditRecorder(),
        correction_resolver=NoMatchCorrectionResolver(),
    )

    outcome = await service.handle_feedback(
        actor=ACTOR,
        text="不是这个，是不存在的灯",
        message_id="message-correction",
        now=NOW + timedelta(minutes=1),
    )

    assert outcome.handled is True
    assert outcome.warnings == ("term_correction_unavailable",)
    assert await store.visible_terms(ACTOR, NOW + timedelta(minutes=1)) == []


@pytest.mark.asyncio
async def test_term_store_failure_returns_warning_without_throwing() -> None:
    service = TermLearningService(
        store=FailingTermStore(),
        audit=InMemoryAuditRecorder(),
    )

    outcome = await service.record_success(
        actor=ACTOR,
        expression="床头灯",
        target=verified("light.bedside"),
        execution=successful_batch("light.bedside"),
        source_message_id="message-1",
        now=NOW,
    )

    assert outcome.learned is False
    assert outcome.warnings == ("term_learning_unavailable",)

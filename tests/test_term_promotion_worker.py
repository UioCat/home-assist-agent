from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import ActorContext
from home_assist_agent.terms.models import (
    HomePromotionStatus,
    TermScope,
    TermStatus,
)
from home_assist_agent.terms.service import TermLearningService
from home_assist_agent.terms.store import SQLiteTermStore
from home_assist_agent.terms.worker import TermPromotionWorker


NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
NOW_PLUS_10 = NOW + timedelta(minutes=10)
PERSON_1 = ActorContext(home_id="home-1", person_id="person-1")
PERSON_2 = ActorContext(home_id="home-1", person_id="person-2")


async def provisional(
    store: SQLiteTermStore,
    *,
    term: str = "床头灯",
    entity_id: str = "light.left",
):
    return await store.create_provisional(
        actor=PERSON_1,
        display_term=term,
        entity_ids=(entity_id,),
        source_message_id="message-original",
        source_candidate_id="cand-01",
        catalog_version="catalog-v1",
        now=NOW,
    )


@pytest.mark.asyncio
async def test_due_provisional_is_approved_once_with_system_message_id(
    tmp_path: Path,
) -> None:
    audit = InMemoryAuditRecorder()
    store = SQLiteTermStore(tmp_path / "terms.db", audit=audit)
    mapping = await provisional(store)
    worker = TermPromotionWorker(store=store, audit=audit)

    first = await worker.run_once(NOW_PLUS_10)
    second = await worker.run_once(NOW_PLUS_10)

    visible = await store.visible_terms(PERSON_1, NOW_PLUS_10)
    assert visible[0].status == TermStatus.APPROVED
    assert first.approved_mapping_ids == (mapping.mapping_id,)
    assert second.approved_mapping_ids == ()
    promotion_requests = [
        event for event in audit.events if event.event_type == "system.request"
    ]
    assert len(promotion_requests) == 1
    assert promotion_requests[0].message_id == (
        f"term-promote-{mapping.mapping_id}-{mapping.revision}"
    )
    promotion_events = [
        event.event_type
        for event in audit.events
        if event.message_id == promotion_requests[0].message_id
    ]
    assert promotion_events == [
        "system.request",
        "term.promotion_checked",
        "term.write.request",
        "term.approved",
        "system.response",
    ]


@pytest.mark.asyncio
async def test_rejected_mapping_is_not_promoted(tmp_path: Path) -> None:
    audit = InMemoryAuditRecorder()
    store = SQLiteTermStore(tmp_path / "terms.db", audit=audit)
    mapping = await provisional(store)
    await store.reject(
        mapping.mapping_id,
        "message-correction",
        now=NOW + timedelta(minutes=1),
    )
    worker = TermPromotionWorker(store=store, audit=audit)

    summary = await worker.run_once(NOW_PLUS_10)

    assert summary.approved_mapping_ids == ()
    assert await store.visible_terms(PERSON_1, NOW_PLUS_10) == []
    assert not any(
        event.event_type == "system.request" for event in audit.events
    )


class FailingAuditRecorder:
    async def record(self, **kwargs):
        raise DependencyError("audit_unavailable", "审计记录写入失败。")


@pytest.mark.asyncio
async def test_worker_audit_failure_prevents_promotion_mutation(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    mapping = await provisional(store)
    worker = TermPromotionWorker(
        store=store,
        audit=FailingAuditRecorder(),
    )

    with pytest.raises(DependencyError) as captured:
        await worker.run_once(NOW_PLUS_10)

    assert captured.value.code == "audit_unavailable"
    current = await store.current_mapping(mapping.mapping_id)
    assert current is not None
    assert current.status == TermStatus.PROVISIONAL


@pytest.mark.asyncio
async def test_household_request_requires_separate_confirmation(
    tmp_path: Path,
) -> None:
    audit = InMemoryAuditRecorder()
    store = SQLiteTermStore(tmp_path / "terms.db", audit=audit)
    await store.create_approved(
        actor=PERSON_1,
        scope=TermScope.PERSON,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-personal",
        source_candidate_id="cand-01",
        catalog_version="catalog-v1",
        now=NOW,
    )
    service = TermLearningService(store=store, audit=audit)

    requested = await service.request_home_promotion(
        actor=PERSON_1,
        text="全家都这么叫",
        message_id="message-share",
        now=NOW,
    )

    assert requested.handled is True
    assert requested.requires_confirmation is True
    assert requested.request is not None
    assert requested.request.status == HomePromotionStatus.PENDING
    assert await store.visible_terms(PERSON_2, NOW) == []

    confirmed = await service.confirm_home_promotion(
        actor=PERSON_1,
        text="确认",
        message_id="message-confirm",
        now=NOW + timedelta(minutes=1),
    )

    assert confirmed.handled is True
    assert confirmed.requires_confirmation is False
    shared = await store.visible_terms(PERSON_2, NOW + timedelta(minutes=1))
    assert len(shared) == 1
    assert shared[0].scope == TermScope.HOME
    assert shared[0].target_entity_ids == ("light.left",)


@pytest.mark.asyncio
async def test_household_confirmation_expires_after_ten_minutes(
    tmp_path: Path,
) -> None:
    audit = InMemoryAuditRecorder()
    store = SQLiteTermStore(tmp_path / "terms.db", audit=audit)
    await store.create_approved(
        actor=PERSON_1,
        scope=TermScope.PERSON,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-personal",
        source_candidate_id="cand-01",
        catalog_version="catalog-v1",
        now=NOW,
    )
    service = TermLearningService(store=store, audit=audit)
    requested = await service.request_home_promotion(
        actor=PERSON_1,
        text="全家都这么叫",
        message_id="message-share",
        now=NOW,
    )

    expired = await service.confirm_home_promotion(
        actor=PERSON_1,
        text="确认",
        message_id="message-late",
        now=NOW_PLUS_10,
    )

    assert expired.handled is True
    assert expired.warnings == ("home_promotion_expired",)
    current = await store.current_home_promotion(
        requested.request.promotion_id
    )
    assert current is not None
    assert current.status == HomePromotionStatus.EXPIRED
    assert await store.visible_terms(PERSON_2, NOW_PLUS_10) == []


@pytest.mark.asyncio
async def test_household_conflict_is_reported_without_overwrite(
    tmp_path: Path,
) -> None:
    audit = InMemoryAuditRecorder()
    store = SQLiteTermStore(tmp_path / "terms.db", audit=audit)
    existing = await store.create_approved(
        actor=PERSON_1,
        scope=TermScope.HOME,
        display_term="床头灯",
        entity_ids=("light.shared_old",),
        source_message_id="message-home-old",
        source_candidate_id="cand-old",
        catalog_version="catalog-v1",
        now=NOW,
    )
    await store.create_approved(
        actor=PERSON_1,
        scope=TermScope.PERSON,
        display_term="床头灯",
        entity_ids=("light.personal_new",),
        source_message_id="message-personal",
        source_candidate_id="cand-new",
        catalog_version="catalog-v2",
        now=NOW + timedelta(minutes=1),
    )
    service = TermLearningService(store=store, audit=audit)
    await service.request_home_promotion(
        actor=PERSON_1,
        text="全家都这么叫",
        message_id="message-share",
        now=NOW + timedelta(minutes=1),
    )

    conflict = await service.confirm_home_promotion(
        actor=PERSON_1,
        text="确认",
        message_id="message-confirm",
        now=NOW + timedelta(minutes=2),
    )

    assert conflict.handled is True
    assert conflict.warnings == ("home_term_conflict",)
    assert conflict.conflict_existing_entity_ids == ("light.shared_old",)
    assert conflict.conflict_requested_entity_ids == ("light.personal_new",)
    visible = await store.visible_terms(PERSON_2, NOW + timedelta(minutes=2))
    assert visible[0].mapping_id == existing.mapping_id

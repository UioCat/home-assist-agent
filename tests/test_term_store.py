from datetime import UTC, datetime, timedelta
from pathlib import Path
import stat

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import (
    ActorContext,
    ClarificationChoice,
    TargetCandidate,
)
from home_assist_agent.terms.models import (
    ResolutionAttempt,
    TermScope,
    TermStatus,
)
from home_assist_agent.terms.store import SQLiteTermStore, TermConflictError


NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
NOW_PLUS_10 = NOW + timedelta(minutes=10)
PERSON_1 = ActorContext(home_id="home-1", person_id="person-1")
PERSON_2 = ActorContext(home_id="home-1", person_id="person-2")


@pytest.mark.asyncio
async def test_personal_term_approval_appends_revision_and_keeps_history(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    first = await store.create_provisional(
        actor=PERSON_1,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-1",
        source_candidate_id="cand-1",
        catalog_version="v1",
        now=NOW,
    )

    approved = await store.approve(
        first.mapping_id,
        "message-promote",
        now=NOW_PLUS_10,
    )
    visible = await store.visible_terms(PERSON_1, NOW_PLUS_10)

    assert approved.status == TermStatus.APPROVED
    assert visible[0].status == TermStatus.APPROVED
    assert visible[0].target_entity_ids == ("light.left",)
    assert await store.revision_count(first.mapping_id) == 2


@pytest.mark.asyncio
async def test_personal_terms_are_isolated_and_precede_home_terms(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    await store.create_approved(
        actor=PERSON_1,
        scope=TermScope.HOME,
        display_term="床头灯",
        entity_ids=("light.shared",),
        source_message_id="message-home",
        source_candidate_id="cand-home",
        catalog_version="v1",
        now=NOW,
    )
    await store.create_approved(
        actor=PERSON_1,
        scope=TermScope.PERSON,
        display_term="床头灯",
        entity_ids=("light.personal",),
        source_message_id="message-personal",
        source_candidate_id="cand-personal",
        catalog_version="v1",
        now=NOW,
    )
    await store.create_approved(
        actor=PERSON_2,
        scope=TermScope.PERSON,
        display_term="二号的灯",
        entity_ids=("light.person_2",),
        source_message_id="message-person-2",
        source_candidate_id="cand-person-2",
        catalog_version="v1",
        now=NOW,
    )

    visible = await store.visible_terms(PERSON_1, NOW)

    assert [
        (mapping.scope, mapping.target_entity_ids) for mapping in visible
    ] == [
        (TermScope.PERSON, ("light.personal",)),
        (TermScope.HOME, ("light.shared",)),
    ]
    assert all(mapping.person_id != "person-2" for mapping in visible)


@pytest.mark.asyncio
async def test_same_provisional_target_reuses_mapping_and_original_window(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    first = await store.create_provisional(
        actor=PERSON_1,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-1",
        source_candidate_id="cand-1",
        catalog_version="v1",
        now=NOW,
    )
    reused = await store.create_provisional(
        actor=PERSON_1,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-2",
        source_candidate_id="cand-2",
        catalog_version="v2",
        now=NOW + timedelta(minutes=3),
    )

    assert reused.mapping_id == first.mapping_id
    assert reused.promote_at == NOW_PLUS_10
    assert reused.revision == 2
    assert await store.revision_count(first.mapping_id) == 2


@pytest.mark.asyncio
async def test_conflicting_approved_term_is_not_overwritten(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    approved = await store.create_approved(
        actor=PERSON_1,
        scope=TermScope.PERSON,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-approved",
        source_candidate_id="cand-left",
        catalog_version="v1",
        now=NOW,
    )

    with pytest.raises(TermConflictError) as captured:
        await store.create_provisional(
            actor=PERSON_1,
            display_term="床头灯",
            entity_ids=("light.right",),
            source_message_id="message-new",
            source_candidate_id="cand-right",
            catalog_version="v1",
            now=NOW + timedelta(minutes=1),
        )

    assert captured.value.existing.mapping_id == approved.mapping_id
    assert await store.revision_count(approved.mapping_id) == 1
    assert (await store.visible_terms(PERSON_1, NOW))[0].target_entity_ids == (
        "light.left",
    )


@pytest.mark.asyncio
async def test_rejection_is_append_only_and_removes_current_visibility(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    mapping = await store.create_provisional(
        actor=PERSON_1,
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-1",
        source_candidate_id="cand-1",
        catalog_version="v1",
        now=NOW,
    )

    rejected = await store.reject(
        mapping.mapping_id,
        "message-correction",
        now=NOW + timedelta(minutes=1),
    )

    assert rejected.status == TermStatus.REJECTED
    assert await store.revision_count(mapping.mapping_id) == 2
    assert await store.visible_terms(PERSON_1, NOW_PLUS_10) == []


def candidate() -> TargetCandidate:
    return TargetCandidate(
        candidate_id="cand_01",
        target_entity_ids=("light.left",),
        display_name="左侧台灯",
        areas=("卧室",),
        domains=("light",),
        states=("off",),
        sources=("ha_name",),
        matched_terms=("左侧台灯",),
        rule_score=350,
        catalog_version="v1",
        home_id="home-1",
    )


@pytest.mark.asyncio
async def test_resolution_attempt_expires_without_deleting_history(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=InMemoryAuditRecorder(),
    )
    attempt = ResolutionAttempt(
        attempt_id="attempt-1",
        source_message_id="message-1",
        home_id="home-1",
        person_id="person-1",
        original_command="打开床头灯",
        category="direct_iot",
        action="turn_on",
        target_expression="床头灯",
        candidates=(candidate(),),
        choices=(
            ClarificationChoice(
                choice_id="choice_1",
                display_name="左侧台灯",
                area_name="卧室",
                domain="light",
            ),
        ),
        choice_candidate_ids=("cand_01",),
        created_at=NOW,
        expires_at=NOW_PLUS_10,
    )

    await store.save_resolution_attempt(attempt, "message-1")

    assert (
        await store.load_resolution_attempt(
            PERSON_1,
            "attempt-1",
            NOW_PLUS_10 - timedelta(seconds=1),
        )
        == attempt
    )
    assert (
        await store.load_resolution_attempt(
            PERSON_1,
            "attempt-1",
            NOW_PLUS_10,
        )
        is None
    )
    assert await store.resolution_attempt_count("attempt-1") == 1


@pytest.mark.asyncio
async def test_database_file_permissions_are_owner_only(tmp_path: Path) -> None:
    database_path = tmp_path / "terms.db"
    store = SQLiteTermStore(
        database_path,
        audit=InMemoryAuditRecorder(),
    )

    await store.visible_terms(PERSON_1, NOW)

    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


class FailingAuditRecorder:
    async def record(self, **kwargs):
        raise DependencyError("audit_unavailable", "审计记录写入失败。")


@pytest.mark.asyncio
async def test_audit_failure_prevents_term_mutation(tmp_path: Path) -> None:
    store = SQLiteTermStore(
        tmp_path / "terms.db",
        audit=FailingAuditRecorder(),
    )

    with pytest.raises(DependencyError) as captured:
        await store.create_provisional(
            actor=PERSON_1,
            display_term="床头灯",
            entity_ids=("light.left",),
            source_message_id="message-1",
            source_candidate_id="cand-1",
            catalog_version="v1",
            now=NOW,
        )

    assert captured.value.code == "audit_unavailable"
    assert await store.revision_count("missing") == 0

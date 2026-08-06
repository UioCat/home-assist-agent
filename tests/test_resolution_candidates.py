from datetime import UTC, datetime

import pytest

from home_assist_agent.resolution.candidates import CandidateBuilder
from home_assist_agent.resolution.models import (
    ActorContext,
    CatalogSnapshot,
    DeviceActionIntent,
    HaEntitySnapshot,
    VisibleTermMapping,
)
from home_assist_agent.resolution.normalize import normalize_term


NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)


def entity(
    entity_id: str,
    *,
    friendly_name: str,
    aliases: tuple[str, ...] = (),
    original_name: str | None = None,
    device_name: str | None = None,
    area_name: str | None = "卧室",
    capabilities: frozenset[str] = frozenset({"turn_on", "turn_off"}),
    available: bool = True,
    disabled: bool = False,
    home_id: str = "home-1",
) -> HaEntitySnapshot:
    return HaEntitySnapshot(
        home_id=home_id,
        entity_id=entity_id,
        domain=entity_id.split(".", maxsplit=1)[0],
        friendly_name=friendly_name,
        original_name=original_name,
        aliases=aliases,
        device_name=device_name,
        area_name=area_name,
        state="off" if available else "unavailable",
        capabilities=capabilities,
        available=available,
        disabled=disabled,
    )


def catalog_with(*entities: HaEntitySnapshot) -> CatalogSnapshot:
    return CatalogSnapshot(
        home_id="home-1",
        catalog_version="catalog-v1",
        observed_at=NOW,
        entities=entities,
    )


def visible_term(
    *,
    term: str,
    entity_ids: tuple[str, ...],
    status: str,
    scope: str = "person",
    person_id: str | None = "person-1",
) -> VisibleTermMapping:
    return VisibleTermMapping(
        mapping_id=f"map-{status}-{term}",
        home_id="home-1",
        person_id=person_id,
        scope=scope,
        display_term=term,
        normalized_term=normalize_term(term),
        target_entity_ids=entity_ids,
        status=status,
    )


def intent(
    expression: str,
    *,
    action: str = "turn_on",
) -> DeviceActionIntent:
    return DeviceActionIntent(
        action=action,
        target_expression=expression,
        parameters={"brightness": 40} if action == "set_brightness" else {},
    )


ACTOR = ActorContext(home_id="home-1", person_id="person-1")


def test_normalize_term_applies_nfkc_casefold_whitespace_and_outer_punctuation() -> None:
    assert normalize_term("  Ｂｅｄ　Ｌｉｇｈｔ！！ ") == "bed light"
    assert normalize_term("「 床头灯 」") == "床头灯"


def test_personal_provisional_precedes_ha_alias_for_same_expression() -> None:
    candidates = CandidateBuilder(limit=20).build(
        intent=intent(" 床头灯 "),
        actor=ACTOR,
        catalog=catalog_with(
            entity("light.left", friendly_name="左侧台灯", aliases=("床头灯",)),
            entity("light.right", friendly_name="右侧台灯", aliases=("床头灯",)),
        ),
        terms=[
            visible_term(
                term="床头灯",
                entity_ids=("light.right",),
                status="provisional",
            )
        ],
    )

    assert candidates[0].candidate_id == "cand_01"
    assert candidates[0].target_entity_ids == ("light.right",)
    assert candidates[0].sources[0] == "personal_provisional"
    assert len(candidates) == 2


def test_action_capability_and_availability_are_fail_closed_filters() -> None:
    candidates = CandidateBuilder().build(
        intent=intent("卧室灯", action="set_brightness"),
        actor=ACTOR,
        catalog=catalog_with(
            entity(
                "light.supported",
                friendly_name="卧室灯",
                capabilities=frozenset(
                    {"turn_on", "turn_off", "set_brightness"}
                ),
            ),
            entity("switch.no_brightness", friendly_name="卧室灯"),
            entity(
                "light.unavailable",
                friendly_name="卧室灯",
                capabilities=frozenset({"set_brightness"}),
                available=False,
            ),
            entity(
                "light.disabled",
                friendly_name="卧室灯",
                capabilities=frozenset({"set_brightness"}),
                disabled=True,
            ),
        ),
        terms=[],
    )

    assert [candidate.target_entity_ids for candidate in candidates] == [
        ("light.supported",)
    ]


def test_duplicate_target_sets_merge_evidence_without_changing_precedence() -> None:
    candidates = CandidateBuilder().build(
        intent=intent("床头灯"),
        actor=ACTOR,
        catalog=catalog_with(
            entity(
                "light.bedside",
                friendly_name="床头灯",
                aliases=("床头灯",),
            )
        ),
        terms=[
            visible_term(
                term="床头灯",
                entity_ids=("light.bedside",),
                status="approved",
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].sources == (
        "personal_approved",
        "ha_alias",
        "ha_name",
    )
    assert candidates[0].matched_terms == ("床头灯",)


def test_personal_approved_precedes_home_shared_then_ha_name() -> None:
    candidates = CandidateBuilder().build(
        intent=intent("阅读灯"),
        actor=ACTOR,
        catalog=catalog_with(
            entity("light.personal", friendly_name="个人灯"),
            entity("light.shared", friendly_name="共享灯"),
            entity("light.ha", friendly_name="阅读灯"),
        ),
        terms=[
            visible_term(
                term="阅读灯",
                entity_ids=("light.shared",),
                status="approved",
                scope="home",
                person_id=None,
            ),
            visible_term(
                term="阅读灯",
                entity_ids=("light.personal",),
                status="approved",
            ),
        ],
    )

    assert [candidate.target_entity_ids for candidate in candidates] == [
        ("light.personal",),
        ("light.shared",),
        ("light.ha",),
    ]
    assert [candidate.sources[0] for candidate in candidates] == [
        "personal_approved",
        "home_shared",
        "ha_name",
    ]


def test_area_match_produces_one_stable_entity_set() -> None:
    candidates = CandidateBuilder().build(
        intent=intent("卧室"),
        actor=ACTOR,
        catalog=catalog_with(
            entity("light.left", friendly_name="左灯"),
            entity("light.right", friendly_name="右灯"),
        ),
        terms=[],
    )

    assert len(candidates) == 1
    assert candidates[0].target_entity_ids == (
        "light.left",
        "light.right",
    )
    assert candidates[0].sources == ("area",)


def test_missing_exact_light_name_builds_semantic_fallback_candidates() -> None:
    candidates = CandidateBuilder().build(
        intent=intent("床头灯"),
        actor=ACTOR,
        catalog=catalog_with(
            entity("light.inner", friendly_name="靠内灯", aliases=()),
            entity("light.outer", friendly_name="靠外灯", aliases=()),
        ),
        terms=[],
    )

    assert [candidate.target_entity_ids for candidate in candidates] == [
        ("light.inner",),
        ("light.outer",),
    ]
    assert [candidate.sources for candidate in candidates] == [
        ("semantic_fallback",),
        ("semantic_fallback",),
    ]
    assert [candidate.matched_terms for candidate in candidates] == [(), ()]


def test_light_fallback_excludes_other_domains_and_unavailable_entities() -> None:
    candidates = CandidateBuilder().build(
        intent=intent("床头灯"),
        actor=ACTOR,
        catalog=catalog_with(
            entity("light.inner", friendly_name="靠内灯", aliases=()),
            entity(
                "light.unavailable",
                friendly_name="靠外灯",
                aliases=(),
                available=False,
            ),
            entity(
                "switch.outlet",
                friendly_name="床边插座",
                aliases=(),
            ),
        ),
        terms=[],
    )

    assert [candidate.target_entity_ids for candidate in candidates] == [
        ("light.inner",),
    ]


def test_semantic_fallback_uses_text_similarity_before_candidate_limit() -> None:
    candidates = CandidateBuilder(limit=2).build(
        intent=intent("床头灯"),
        actor=ACTOR,
        catalog=catalog_with(
            entity(
                "light.a_indicator",
                friendly_name="电脑 指示灯",
                aliases=(),
            ),
            entity(
                "light.y_outer",
                friendly_name="靠外灯",
                aliases=(),
            ),
            entity(
                "light.z_inner",
                friendly_name="靠内灯",
                aliases=(),
            ),
        ),
        terms=[],
    )

    assert {
        candidate.target_entity_ids for candidate in candidates
    } == {
        ("light.y_outer",),
        ("light.z_inner",),
    }


def test_candidate_display_name_removes_only_generic_integration_suffix() -> None:
    candidates = CandidateBuilder().build(
        intent=intent("床头灯"),
        actor=ACTOR,
        catalog=catalog_with(
            entity(
                "light.inner",
                friendly_name="靠内灯  灯",
                original_name="灯",
                device_name="靠内灯",
                aliases=(),
            ),
            entity(
                "light.indicator",
                friendly_name="电脑  指示灯",
                original_name="指示灯",
                device_name="电脑",
                aliases=(),
            ),
        ),
        terms=[],
    )

    display_by_entity = {
        candidate.target_entity_ids[0]: candidate.display_name
        for candidate in candidates
    }
    assert display_by_entity == {
        "light.inner": "靠内灯",
        "light.indicator": "电脑  指示灯",
    }


def test_candidate_and_entity_set_limits_are_enforced() -> None:
    many_entities = tuple(
        entity(
            f"light.room_{index:02d}",
            friendly_name="灯",
            area_name="大房间",
        )
        for index in range(25)
    )

    candidates = CandidateBuilder(limit=3, target_limit=20).build(
        intent=intent("灯"),
        actor=ACTOR,
        catalog=catalog_with(*many_entities),
        terms=[],
    )

    assert len(candidates) == 3

    area_candidates = CandidateBuilder(limit=20, target_limit=20).build(
        intent=intent("大房间"),
        actor=ACTOR,
        catalog=catalog_with(*many_entities),
        terms=[],
    )
    assert len(area_candidates) == 20
    assert all(
        len(candidate.target_entity_ids) == 1
        for candidate in area_candidates
    )


def test_invisible_personal_terms_do_not_create_term_backed_candidates() -> None:
    candidates = CandidateBuilder().build(
        intent=intent("我的灯"),
        actor=ACTOR,
        catalog=catalog_with(
            entity("light.mine", friendly_name="普通灯"),
        ),
        terms=[
            visible_term(
                term="我的灯",
                entity_ids=("light.mine",),
                status="approved",
                person_id="person-2",
            )
        ],
    )

    assert [candidate.target_entity_ids for candidate in candidates] == [
        ("light.mine",),
    ]
    assert candidates[0].sources == ("semantic_fallback",)


def test_catalog_contract_rejects_cross_home_entities() -> None:
    with pytest.raises(ValueError, match="another home"):
        catalog_with(
            entity("light.mine", friendly_name="普通灯"),
            entity(
                "light.other_home",
                friendly_name="我的灯",
                home_id="home-2",
            ),
        )


def test_invalid_builder_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        CandidateBuilder(limit=0)
    with pytest.raises(ValueError, match="target_limit"):
        CandidateBuilder(target_limit=21)

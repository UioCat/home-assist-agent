from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.codex.gateway import CodexGateway
from home_assist_agent.commands.models import (
    AnswerResult,
    DeviceCommand,
    DevicePlanResult,
    RouteDecision,
    ToolDefinition,
    ToolExecutionResult,
    ToolPlan,
)
from home_assist_agent.commands.service import CommandOrchestrator
from home_assist_agent.devices.executor import DeviceExecutor
from home_assist_agent.resolution.candidates import CandidateBuilder
from home_assist_agent.resolution.models import (
    ActorContext,
    CatalogSnapshot,
    HaEntitySnapshot,
    TargetResolutionDecision,
)
from home_assist_agent.resolution.verifier import ResolutionVerifier
from home_assist_agent.terms.models import ResolutionAttempt
from home_assist_agent.terms.models import (
    FeedbackOutcome,
    HomePromotionOutcome,
    TermLearningOutcome,
)


NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
ACTOR = ActorContext(home_id="home-1", person_id="person-1")


def entity(
    entity_id: str,
    *,
    friendly_name: str,
    aliases: tuple[str, ...] = ("床头灯",),
) -> HaEntitySnapshot:
    return HaEntitySnapshot(
        home_id="home-1",
        entity_id=entity_id,
        domain="light",
        friendly_name=friendly_name,
        aliases=aliases,
        area_name="卧室",
        state="off",
        capabilities=frozenset({"turn_on", "turn_off", "set_brightness"}),
        available=True,
    )


def snapshot(
    *entities: HaEntitySnapshot,
    version: str = "catalog-v1",
) -> CatalogSnapshot:
    return CatalogSnapshot(
        home_id="home-1",
        catalog_version=version,
        observed_at=NOW,
        entities=entities,
    )


class SequencedCatalog:
    def __init__(self, *snapshots: CatalogSnapshot) -> None:
        self.snapshots = list(snapshots)
        self.calls: list[str] = []

    async def snapshot(
        self,
        actor: ActorContext,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> CatalogSnapshot:
        self.calls.append(message_id)
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


@dataclass
class FakeRouter:
    decision: RouteDecision
    calls: list[str] = field(default_factory=list)

    async def route(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> RouteDecision:
        self.calls.append(command)
        return self.decision


class FakeTerms:
    def __init__(self) -> None:
        self.attempts: list[ResolutionAttempt] = []

    async def visible_terms(
        self,
        actor: ActorContext,
        now: datetime,
    ) -> list[Any]:
        return []

    async def save_resolution_attempt(
        self,
        attempt: ResolutionAttempt,
        message_id: str,
    ) -> ResolutionAttempt:
        self.attempts.append(attempt)
        return attempt

    async def load_latest_resolution_attempt(
        self,
        actor: ActorContext,
        now: datetime,
    ) -> ResolutionAttempt | None:
        for attempt in reversed(self.attempts):
            if (
                attempt.home_id == actor.home_id
                and attempt.person_id == actor.person_id
                and attempt.expires_at > now
            ):
                return attempt
        return None


@dataclass
class FakeCodex:
    decisions: list[TargetResolutionDecision]
    plan_result: DevicePlanResult | None = None
    resolve_calls: list[list[str]] = field(default_factory=list)
    plan_calls: list[str] = field(default_factory=list)

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
        self.resolve_calls.append(
            [candidate.candidate_id for candidate in candidates]
        )
        if len(self.decisions) > 1:
            return self.decisions.pop(0)
        return self.decisions[0]

    async def plan_device_control(
        self,
        command: str,
        intent_summary: str,
        tools: list[ToolDefinition],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> DevicePlanResult:
        self.plan_calls.append(command)
        assert self.plan_result is not None
        return self.plan_result

    async def answer(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AnswerResult:
        return AnswerResult(message="普通回答")


class RecordingMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], str]] = []
        self.list_calls = 0

    async def list_tools(
        self,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[ToolDefinition]:
        self.list_calls += 1
        return [
            ToolDefinition(
                name="assist.HassTurnOn",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            ToolDefinition(
                name="assist.HassLightSet",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "brightness": {"type": "integer"},
                    },
                    "required": ["name", "brightness"],
                },
            ),
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ToolExecutionResult:
        self.calls.append((name, arguments, message_id))
        return ToolExecutionResult(tool_name=name, content="Done")


def selected(
    candidate_id: str = "cand_01",
    *,
    confidence: float = 0.95,
) -> TargetResolutionDecision:
    return TargetResolutionDecision(
        status="selected",
        selected_candidate_id=candidate_id,
        confidence=confidence,
        alternative_candidate_ids=(),
        reason="语义匹配",
    )


def ambiguous() -> TargetResolutionDecision:
    return TargetResolutionDecision(
        status="ambiguous",
        selected_candidate_id=None,
        confidence=0.55,
        alternative_candidate_ids=("cand_01", "cand_02"),
        reason="两个灯都叫床头灯",
    )


def build_service(
    *,
    decision: RouteDecision,
    catalog: SequencedCatalog,
    codex: FakeCodex,
    terms: FakeTerms | None = None,
    term_learning=None,
) -> tuple[
    CommandOrchestrator,
    RecordingMcp,
    InMemoryAuditRecorder,
    FakeTerms,
    FakeRouter,
]:
    audit = InMemoryAuditRecorder()
    mcp = RecordingMcp()
    active_terms = terms or FakeTerms()
    router = FakeRouter(decision)
    service = CommandOrchestrator(
        router=router,
        codex=codex,
        devices=DeviceExecutor(mcp),
        catalog=catalog,
        term_store=active_terms,
        candidate_builder=CandidateBuilder(),
        verifier=ResolutionVerifier(
            catalog=catalog,
            audit=audit,
            confidence_threshold=0.80,
        ),
        audit=audit,
        target_resolution_enabled=True,
        clock=lambda: NOW,
        term_learning=term_learning,
    )
    return service, mcp, audit, active_terms, router


class RecordingTermLearning:
    def __init__(
        self,
        *,
        feedback: FeedbackOutcome | None = None,
        warning: str | None = None,
        home_outcomes: list[HomePromotionOutcome] | None = None,
    ) -> None:
        self.feedback = feedback or FeedbackOutcome(handled=False)
        self.warning = warning
        self.home_outcomes = list(home_outcomes or [])
        self.record_calls: list[dict[str, Any]] = []
        self.feedback_calls: list[str] = []

    async def handle_feedback(
        self,
        *,
        actor: ActorContext,
        text: str,
        message_id: str,
        now: datetime,
    ) -> FeedbackOutcome:
        self.feedback_calls.append(text)
        return self.feedback

    async def record_success(self, **kwargs) -> TermLearningOutcome:
        self.record_calls.append(kwargs)
        return TermLearningOutcome(
            warnings=((self.warning,) if self.warning else ()),
        )

    async def confirm_home_promotion(self, **kwargs) -> HomePromotionOutcome:
        if (
            self.home_outcomes
            and not self.home_outcomes[0].requires_confirmation
        ):
            return self.home_outcomes.pop(0)
        return HomePromotionOutcome(handled=False)

    async def request_home_promotion(self, **kwargs) -> HomePromotionOutcome:
        if self.home_outcomes:
            return self.home_outcomes.pop(0)
        return HomePromotionOutcome(handled=False)


@pytest.mark.asyncio
async def test_open_bedside_light_resolves_before_execution() -> None:
    catalog = SequencedCatalog(
        snapshot(entity("light.bedside", friendly_name="左侧台灯")),
        snapshot(entity("light.bedside", friendly_name="左侧台灯")),
    )
    service, mcp, audit, _, _ = build_service(
        decision=RouteDecision(
            category="direct_iot",
            device_command=DeviceCommand(
                action="turn_on",
                target_expression="床头灯",
            ),
        ),
        catalog=catalog,
        codex=FakeCodex([selected()]),
    )

    response = await service.execute(
        "打开床头灯",
        "message-1",
        actor=ACTOR,
    )

    assert response.status == "success"
    assert response.tool_call is not None
    assert response.tool_call.arguments["name"] == "light.bedside"
    assert mcp.calls[0][1]["name"] == "light.bedside"
    assert "床头灯" not in json_arguments(mcp.calls)
    assert any(
        event.event_type == "target.candidates_generated"
        for event in audit.events
    )


@pytest.mark.asyncio
async def test_full_success_hands_off_learning_once_without_reexecution() -> None:
    catalog = SequencedCatalog(
        snapshot(entity("light.bedside", friendly_name="左侧台灯")),
        snapshot(entity("light.bedside", friendly_name="左侧台灯")),
    )
    learning = RecordingTermLearning(warning="term_learning_unavailable")
    service, mcp, _, _, _ = build_service(
        decision=RouteDecision(
            category="direct_iot",
            device_command=DeviceCommand(
                action="turn_on",
                target_expression="床头灯",
            ),
        ),
        catalog=catalog,
        codex=FakeCodex([selected()]),
        term_learning=learning,
    )

    response = await service.execute(
        "打开床头灯",
        "message-learning",
        actor=ACTOR,
    )

    assert response.status == "success"
    assert response.warnings == ["term_learning_unavailable"]
    assert len(learning.record_calls) == 1
    assert learning.record_calls[0]["expression"] == "床头灯"
    assert len(mcp.calls) == 1


@pytest.mark.asyncio
async def test_feedback_is_handled_before_router_and_device_pipeline() -> None:
    learning = RecordingTermLearning(
        feedback=FeedbackOutcome(
            handled=True,
            message="已撤销刚才学习的个人称呼。",
        )
    )
    catalog = SequencedCatalog(
        snapshot(entity("light.bedside", friendly_name="左侧台灯"))
    )
    service, mcp, _, _, router = build_service(
        decision=RouteDecision(category="other"),
        catalog=catalog,
        codex=FakeCodex([selected()]),
        term_learning=learning,
    )

    response = await service.execute(
        "不是这个",
        "message-feedback",
        actor=ACTOR,
    )

    assert response.status == "success"
    assert response.message == "已撤销刚才学习的个人称呼。"
    assert router.calls == []
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_home_sharing_request_and_confirmation_bypass_device_pipeline() -> None:
    learning = RecordingTermLearning(
        home_outcomes=[
            HomePromotionOutcome(
                handled=True,
                requires_confirmation=True,
                message="确认设为全家共享称呼吗？",
            ),
            HomePromotionOutcome(
                handled=True,
                message="已设为家庭共享称呼。",
            ),
        ]
    )
    catalog = SequencedCatalog(
        snapshot(entity("light.bedside", friendly_name="左侧台灯"))
    )
    service, mcp, _, _, router = build_service(
        decision=RouteDecision(category="other"),
        catalog=catalog,
        codex=FakeCodex([selected()]),
        term_learning=learning,
    )

    request_response = await service.execute(
        "全家都这么叫",
        "message-share",
        actor=ACTOR,
    )
    confirm_response = await service.execute(
        "确认",
        "message-confirm",
        actor=ACTOR,
    )

    assert request_response.status == "needs_input"
    assert confirm_response.status == "success"
    assert router.calls == []
    assert mcp.calls == []


def json_arguments(calls: list[tuple[str, dict[str, object], str]]) -> str:
    return str([arguments for _, arguments, _ in calls])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolution_decision",
    [ambiguous(), selected(confidence=0.70)],
)
async def test_ambiguity_or_low_confidence_returns_choices_without_side_effects(
    resolution_decision: TargetResolutionDecision,
) -> None:
    first = entity("light.left", friendly_name="左侧台灯")
    second = entity("light.right", friendly_name="右侧台灯")
    catalog = SequencedCatalog(snapshot(first, second))
    service, mcp, _, terms, _ = build_service(
        decision=RouteDecision(
            category="direct_iot",
            device_command=DeviceCommand(
                action="turn_on",
                target_expression="床头灯",
            ),
        ),
        catalog=catalog,
        codex=FakeCodex([resolution_decision]),
    )

    response = await service.execute(
        "打开床头灯",
        "message-ambiguous",
        actor=ACTOR,
    )

    assert response.status == "needs_input"
    assert 1 <= len(response.resolution.choices) <= 3
    assert mcp.calls == []
    assert len(terms.attempts) == 1
    assert terms.attempts[0].source_message_id == "message-ambiguous"


@pytest.mark.asyncio
async def test_no_candidates_skips_codex_and_mcp() -> None:
    catalog = SequencedCatalog(
        snapshot(
            entity(
                "light.other",
                friendly_name="客厅主灯",
                aliases=(),
            )
        )
    )
    codex = FakeCodex([selected()])
    service, mcp, _, terms, _ = build_service(
        decision=RouteDecision(
            category="direct_iot",
            device_command=DeviceCommand(
                action="turn_on",
                target_expression="不存在的灯",
            ),
        ),
        catalog=catalog,
        codex=codex,
    )

    response = await service.execute(
        "打开不存在的灯",
        "message-no-candidates",
        actor=ACTOR,
    )

    assert response.status == "needs_input"
    assert response.resolution.status == "no_match"
    assert codex.resolve_calls == []
    assert mcp.calls == []
    assert len(terms.attempts) == 1


@pytest.mark.asyncio
async def test_catalog_change_rebuilds_and_resolves_only_once() -> None:
    old = entity("light.bedside", friendly_name="左侧台灯")
    current = entity("light.bedside", friendly_name="新左侧台灯")
    catalog = SequencedCatalog(
        snapshot(old, version="catalog-v1"),
        snapshot(current, version="catalog-v2"),
        snapshot(current, version="catalog-v2"),
        snapshot(current, version="catalog-v2"),
    )
    codex = FakeCodex([selected(), selected()])
    service, mcp, _, _, _ = build_service(
        decision=RouteDecision(
            category="direct_iot",
            device_command=DeviceCommand(
                action="turn_on",
                target_expression="床头灯",
            ),
        ),
        catalog=catalog,
        codex=codex,
    )

    response = await service.execute(
        "打开床头灯",
        "message-stale",
        actor=ACTOR,
    )

    assert response.status == "success"
    assert len(codex.resolve_calls) == 2
    assert len(catalog.calls) == 4
    assert len(mcp.calls) == 1


@pytest.mark.asyncio
async def test_indirect_plan_cannot_override_resolved_target() -> None:
    catalog = SequencedCatalog(
        snapshot(entity("light.bedside", friendly_name="左侧台灯")),
        snapshot(entity("light.bedside", friendly_name="左侧台灯")),
    )
    route = RouteDecision(
        category="indirect_iot",
        intent_summary="把床头灯调暗",
        target_expression="床头灯",
        indirect_action="set_brightness",
    )
    service, mcp, _, _, _ = build_service(
        decision=route,
        catalog=catalog,
        codex=FakeCodex(
            [selected()],
            plan_result=DevicePlanResult(
                message="已调暗。",
                tool_plan=ToolPlan(
                    tool_name="HassLightSet",
                    arguments={
                        "name": "light.invented",
                        "brightness": 30,
                    },
                ),
            ),
        ),
    )

    response = await service.execute(
        "床头灯太亮了",
        "message-indirect",
        actor=ACTOR,
    )

    assert response.status == "success"
    assert mcp.calls[-1][1] == {
        "brightness": 30,
        "name": "light.bedside",
    }


@pytest.mark.asyncio
async def test_indirect_brightness_plan_requires_brightness_before_mcp_call() -> None:
    catalog = SequencedCatalog(
        snapshot(entity("light.bedside", friendly_name="左侧台灯")),
        snapshot(entity("light.bedside", friendly_name="左侧台灯")),
    )
    service, mcp, _, _, _ = build_service(
        decision=RouteDecision(
            category="indirect_iot",
            intent_summary="把床头灯调暗",
            target_expression="床头灯",
            indirect_action="set_brightness",
        ),
        catalog=catalog,
        codex=FakeCodex(
            [selected()],
            plan_result=DevicePlanResult(
                message="已调暗。",
                tool_plan=ToolPlan(
                    tool_name="HassLightSet",
                    arguments={},
                ),
            ),
        ),
    )

    response = await service.execute(
        "床头灯太亮了",
        "message-missing-brightness",
        actor=ACTOR,
    )

    assert response.status == "error"
    assert response.error_code == "invalid_device_plan_output"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_clarification_followup_uses_new_message_and_original_causation() -> None:
    first = entity("light.left", friendly_name="左侧台灯")
    second = entity("light.right", friendly_name="右侧台灯")
    catalog = SequencedCatalog(
        snapshot(first, second),
        snapshot(first, second),
    )
    terms = FakeTerms()
    router_decision = RouteDecision(
        category="direct_iot",
        device_command=DeviceCommand(
            action="turn_on",
            target_expression="床头灯",
        ),
    )
    service, mcp, _, _, router = build_service(
        decision=router_decision,
        catalog=catalog,
        codex=FakeCodex([ambiguous()]),
        terms=terms,
    )
    first_response = await service.execute(
        "打开床头灯",
        "message-original",
        actor=ACTOR,
    )

    second_response = await service.execute(
        "choice_1",
        "message-followup",
        actor=ACTOR,
    )

    assert first_response.status == "needs_input"
    assert second_response.status == "success"
    assert mcp.calls[-1][2] == "message-followup"
    assert router.calls == ["打开床头灯"]

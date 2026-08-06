from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.commands.models import (
    AnswerResult,
    CommandCategory,
    CommandResponse,
    CommandStatus,
    DevicePlanResult,
    ResolutionDetails,
    RouteDecision,
    ToolPlan,
    ToolCallRecord,
    ToolDefinition,
    TraceStep,
)
from home_assist_agent.devices.executor import (
    DeviceExecutionError,
    DeviceExecutor,
)
from home_assist_agent.errors import DependencyError
from home_assist_agent.ha.catalog import HomeAssistantCatalogProvider
from home_assist_agent.ha.safety import SafetyViolation
from home_assist_agent.resolution.candidates import CandidateBuilder
from home_assist_agent.resolution.models import (
    ActorContext,
    ClarificationChoice,
    DeviceActionIntent,
    ResolutionStatus,
    TargetCandidate,
    TargetResolutionDecision,
)
from home_assist_agent.resolution.normalize import normalize_term
from home_assist_agent.resolution.verifier import (
    ResolutionError,
    ResolutionVerifier,
)
from home_assist_agent.terms.models import (
    FeedbackOutcome,
    HomePromotionOutcome,
    ResolutionAttempt,
    TermLearningOutcome,
    TermMapping,
)


class InstructionRouterProtocol(Protocol):
    async def route(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> RouteDecision: ...


class CodexServiceProtocol(Protocol):
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

    async def plan_device_control(
        self,
        command: str,
        intent_summary: str,
        tools: list[ToolDefinition],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> DevicePlanResult: ...

    async def answer(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AnswerResult: ...


class TermStoreProtocol(Protocol):
    async def visible_terms(
        self,
        actor: ActorContext,
        now: datetime,
    ) -> Sequence[TermMapping]: ...

    async def save_resolution_attempt(
        self,
        attempt: ResolutionAttempt,
        message_id: str,
    ) -> ResolutionAttempt: ...

    async def load_latest_resolution_attempt(
        self,
        actor: ActorContext,
        now: datetime,
    ) -> ResolutionAttempt | None: ...


class TermLearningProtocol(Protocol):
    async def record_success(
        self,
        *,
        actor: ActorContext,
        expression: str,
        target,
        execution,
        source_message_id: str,
        now: datetime,
    ) -> TermLearningOutcome: ...

    async def handle_feedback(
        self,
        *,
        actor: ActorContext,
        text: str,
        message_id: str,
        now: datetime,
    ) -> FeedbackOutcome: ...

    async def request_home_promotion(
        self,
        *,
        actor: ActorContext,
        text: str,
        message_id: str,
        now: datetime,
    ) -> HomePromotionOutcome: ...

    async def confirm_home_promotion(
        self,
        *,
        actor: ActorContext,
        text: str,
        message_id: str,
        now: datetime,
    ) -> HomePromotionOutcome: ...


class CommandOrchestrator:
    def __init__(
        self,
        router: InstructionRouterProtocol,
        codex: CodexServiceProtocol,
        devices: DeviceExecutor,
        *,
        catalog: HomeAssistantCatalogProvider | None = None,
        term_store: TermStoreProtocol | None = None,
        candidate_builder: CandidateBuilder | None = None,
        verifier: ResolutionVerifier | None = None,
        audit: AuditRecorderProtocol | None = None,
        target_resolution_enabled: bool = False,
        clock: Callable[[], datetime] | None = None,
        term_learning: TermLearningProtocol | None = None,
    ) -> None:
        self._router = router
        self._codex = codex
        self._devices = devices
        self._catalog = catalog
        self._term_store = term_store
        self._candidate_builder = candidate_builder
        self._verifier = verifier
        self._audit = audit
        self._target_resolution_enabled = target_resolution_enabled
        self._clock = clock or (lambda: datetime.now(UTC))
        self._term_learning = term_learning
        if target_resolution_enabled and any(
            dependency is None
            for dependency in (
                catalog,
                term_store,
                candidate_builder,
                verifier,
                audit,
            )
        ):
            raise ValueError(
                "target resolution dependencies are required when enabled"
            )

    async def execute(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        actor: ActorContext | None = None,
    ) -> CommandResponse:
        started_at = perf_counter()
        trace = [TraceStep(stage="input", status="success", summary="收到指令")]
        if self._target_resolution_enabled:
            if actor is None:
                return self._response(
                    message_id=message_id,
                    category=CommandCategory.OTHER,
                    route="codex",
                    status=CommandStatus.ERROR,
                    message="缺少可信调用身份。",
                    trace=trace,
                    started_at=started_at,
                    error_code="actor_required",
                )
            if self._term_learning is not None:
                try:
                    home_promotion = (
                        await self._term_learning.confirm_home_promotion(
                            actor=actor,
                            text=command,
                            message_id=message_id,
                            now=self._clock(),
                        )
                    )
                    if not home_promotion.handled:
                        home_promotion = (
                            await self._term_learning.request_home_promotion(
                                actor=actor,
                                text=command,
                                message_id=message_id,
                                now=self._clock(),
                            )
                        )
                    if home_promotion.handled:
                        return self._response(
                            message_id=message_id,
                            category=CommandCategory.OTHER,
                            route="codex",
                            status=(
                                CommandStatus.NEEDS_INPUT
                                if home_promotion.requires_confirmation
                                else CommandStatus.SUCCESS
                            ),
                            message=(
                                home_promotion.message
                                or "已处理家庭共享称呼。"
                            ),
                            trace=trace,
                            started_at=started_at,
                            warnings=list(home_promotion.warnings),
                        )
                    feedback = await self._term_learning.handle_feedback(
                        actor=actor,
                        text=command,
                        message_id=message_id,
                        now=self._clock(),
                    )
                except DependencyError as error:
                    return self._response(
                        message_id=message_id,
                        category=CommandCategory.OTHER,
                        route="codex",
                        status=CommandStatus.ERROR,
                        message=error.message,
                        trace=trace,
                        started_at=started_at,
                        error_code=error.code,
                    )
                if feedback.handled:
                    return self._response(
                        message_id=message_id,
                        category=CommandCategory.OTHER,
                        route="codex",
                        status=CommandStatus.SUCCESS,
                        message=feedback.message or "已处理术语纠正。",
                        trace=trace,
                        started_at=started_at,
                        warnings=list(feedback.warnings),
                    )
            clarification = await self._match_clarification(
                command,
                message_id,
                actor,
                trace,
                started_at,
            )
            if clarification is not None:
                return clarification
        try:
            decision = await self._router.route(
                command,
                message_id,
                correlation_id,
                causation_id,
            )
        except DependencyError as error:
            trace.append(
                TraceStep(
                    stage="classify",
                    status="error",
                    summary=error.message,
                )
            )
            return self._response(
                message_id=message_id,
                category=CommandCategory.OTHER,
                route="codex",
                status=CommandStatus.ERROR,
                message=error.message,
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )

        trace.append(
            TraceStep(
                stage="classify",
                status="success",
                summary=self._classification_summary(decision.category),
            )
        )
        if decision.category == CommandCategory.DIRECT_IOT:
            if self._target_resolution_enabled:
                return await self._execute_resolved(
                    command=command,
                    decision=decision,
                    actor=actor,
                    message_id=message_id,
                    trace=trace,
                    started_at=started_at,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
            return await self._execute_direct(
                decision,
                message_id,
                trace,
                started_at,
                correlation_id,
                causation_id,
            )
        if decision.category == CommandCategory.INDIRECT_IOT:
            if self._target_resolution_enabled:
                return await self._execute_resolved(
                    command=command,
                    decision=decision,
                    actor=actor,
                    message_id=message_id,
                    trace=trace,
                    started_at=started_at,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
            return await self._execute_indirect(
                command,
                decision,
                message_id,
                trace,
                started_at,
                correlation_id,
                causation_id,
            )
        return await self._answer(
            command,
            message_id,
            trace,
            started_at,
            correlation_id,
            causation_id,
        )

    async def _execute_resolved(
        self,
        *,
        command: str,
        decision: RouteDecision,
        actor: ActorContext | None,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> CommandResponse:
        assert actor is not None
        try:
            intent = self._intent_from_route(decision)
        except ValueError as error:
            return self._response(
                message_id=message_id,
                category=decision.category,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message=str(error),
                trace=trace,
                started_at=started_at,
                error_code="invalid_route_output",
            )

        for resolution_round in range(2):
            try:
                catalog = await self._active_catalog.snapshot(
                    actor,
                    message_id,
                    correlation_id,
                    causation_id,
                )
                visible_terms = await self._active_term_store.visible_terms(
                    actor,
                    self._clock(),
                )
                candidates = self._active_candidate_builder.build(
                    intent=intent,
                    actor=actor,
                    catalog=catalog,
                    terms=visible_terms,
                )
                await self._active_audit.record(
                    message_id=message_id,
                    event_type="target.candidates_generated",
                    service="target_resolution",
                    payload={
                        "round": resolution_round + 1,
                        "target_expression": intent.target_expression,
                        "action": intent.action,
                        "generation_mode": (
                            "semantic_fallback"
                            if candidates
                            and all(
                                "semantic_fallback" in candidate.sources
                                for candidate in candidates
                            )
                            else "exact"
                            if candidates
                            else "none"
                        ),
                        "catalog_version": catalog.catalog_version,
                        "candidates": [
                            candidate.model_dump(mode="json")
                            for candidate in candidates
                        ],
                    },
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
            except DependencyError as error:
                return self._device_error_response(
                    error=error,
                    message_id=message_id,
                    category=decision.category,
                    trace=trace,
                    started_at=started_at,
                )

            if not candidates:
                no_match = TargetResolutionDecision(
                    status=ResolutionStatus.NO_MATCH,
                    selected_candidate_id=None,
                    confidence=0,
                    alternative_candidate_ids=(),
                    reason="确定性规则未生成候选",
                )
                return await self._clarification_response(
                    command=command,
                    route_decision=decision,
                    intent=intent,
                    resolution_decision=no_match,
                    candidates=candidates,
                    actor=actor,
                    message_id=message_id,
                    trace=trace,
                    started_at=started_at,
                )

            try:
                resolution = await self._codex.resolve_target(
                    utterance=command,
                    action_intent=intent,
                    candidates=candidates,
                    message_id=message_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                verified = await self._active_verifier.verify(
                    decision=resolution,
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
                return await self._clarification_response(
                    command=command,
                    route_decision=decision,
                    intent=intent,
                    resolution_decision=resolution,
                    candidates=candidates,
                    actor=actor,
                    message_id=message_id,
                    trace=trace,
                    started_at=started_at,
                )
            except DependencyError as error:
                return self._device_error_response(
                    error=error,
                    message_id=message_id,
                    category=decision.category,
                    trace=trace,
                    started_at=started_at,
                )

            return await self._execute_verified_target(
                command=command,
                decision=decision,
                intent=intent,
                verified=verified,
                actor=actor,
                message_id=message_id,
                trace=trace,
                started_at=started_at,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

        raise AssertionError("resolution loop must return")

    async def _execute_verified_target(
        self,
        *,
        command: str,
        decision: RouteDecision,
        intent: DeviceActionIntent,
        verified,
        actor: ActorContext,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> CommandResponse:
        plan: ToolPlan | None = None
        message = "Home Assistant 已处理该指令。"
        execution_intent = intent
        if decision.category == CommandCategory.INDIRECT_IOT:
            assert decision.intent_summary is not None
            try:
                tools = await self._devices.list_safe_tools(
                    message_id,
                    correlation_id,
                    causation_id,
                )
                plan_result = await self._codex.plan_device_control(
                    command,
                    decision.intent_summary,
                    tools,
                    message_id,
                    correlation_id,
                    causation_id,
                )
                clean_parameters = self._strip_target_parameters(
                    plan_result.tool_plan.arguments
                )
                if (
                    intent.action == "set_brightness"
                    and "brightness" not in clean_parameters
                ):
                    raise ValueError("brightness is required")
                execution_intent = DeviceActionIntent(
                    action=intent.action,
                    target_expression=intent.target_expression,
                    parameters=clean_parameters,
                )
                plan = plan_result.tool_plan
                message = plan_result.message
            except (DependencyError, DeviceExecutionError) as error:
                return self._device_error_response(
                    error=error,
                    message_id=message_id,
                    category=decision.category,
                    trace=trace,
                    started_at=started_at,
                )
            except ValueError:
                return self._response(
                    message_id=message_id,
                    category=decision.category,
                    route="home_assistant_mcp",
                    status=CommandStatus.ERROR,
                    message="设备计划缺少有效的动作参数。",
                    trace=trace,
                    started_at=started_at,
                    error_code="invalid_device_plan_output",
                )

        try:
            batch = await self._devices.execute_verified(
                intent=execution_intent,
                target=verified,
                plan=plan,
                message_id=message_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except Exception as error:
            return self._device_error_response(
                error=error,
                message_id=message_id,
                category=decision.category,
                trace=trace,
                started_at=started_at,
            )
        if not batch.fully_succeeded:
            failed_message = (
                batch.failed[0].message if batch.failed else "设备集合执行不完整。"
            )
            trace.append(
                TraceStep(
                    stage="dispatch",
                    status=CommandStatus.ERROR,
                    summary=failed_message,
                )
            )
            return self._response(
                message_id=message_id,
                category=decision.category,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message=failed_message,
                trace=trace,
                started_at=started_at,
                tool_calls=list(batch.tool_calls),
                error_code="partial_device_execution",
            )
        warnings: list[str] = []
        if self._term_learning is not None:
            try:
                learning = await self._term_learning.record_success(
                    actor=actor,
                    expression=intent.target_expression,
                    target=verified,
                    execution=batch,
                    source_message_id=message_id,
                    now=self._clock(),
                )
                warnings.extend(learning.warnings)
            except DependencyError:
                warnings.append("term_learning_unavailable")
        trace.extend(
            [
                TraceStep(
                    stage="resolve",
                    status=CommandStatus.SUCCESS,
                    summary="目标候选已验证",
                ),
                TraceStep(
                    stage="dispatch",
                    status=CommandStatus.SUCCESS,
                    summary="Home Assistant MCP",
                ),
            ]
        )
        return self._response(
            message_id=message_id,
            category=decision.category,
            route="home_assistant_mcp",
            status=CommandStatus.SUCCESS,
            message=message,
            trace=trace,
            started_at=started_at,
            tool_calls=list(batch.tool_calls),
            resolution=ResolutionDetails(
                status=ResolutionStatus.SELECTED,
                confidence=1,
            ),
            warnings=warnings,
        )

    async def _clarification_response(
        self,
        *,
        command: str,
        route_decision: RouteDecision,
        intent: DeviceActionIntent,
        resolution_decision: TargetResolutionDecision,
        candidates: list[TargetCandidate],
        actor: ActorContext,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
    ) -> CommandResponse:
        candidate_by_id = {
            candidate.candidate_id: candidate for candidate in candidates
        }
        preferred_ids: list[str] = []
        if resolution_decision.selected_candidate_id is not None:
            preferred_ids.append(resolution_decision.selected_candidate_id)
        preferred_ids.extend(resolution_decision.alternative_candidate_ids)
        preferred_ids.extend(
            candidate.candidate_id for candidate in candidates
        )
        unique_ids: list[str] = []
        for candidate_id in preferred_ids:
            if candidate_id in candidate_by_id and candidate_id not in unique_ids:
                unique_ids.append(candidate_id)
            if len(unique_ids) == 3:
                break
        choices = tuple(
            ClarificationChoice(
                choice_id=f"choice_{index}",
                display_name=candidate_by_id[candidate_id].display_name,
                area_name=(
                    candidate_by_id[candidate_id].areas[0]
                    if candidate_by_id[candidate_id].areas
                    else None
                ),
                domain=(
                    candidate_by_id[candidate_id].domains[0]
                    if candidate_by_id[candidate_id].domains
                    else "unknown"
                ),
            )
            for index, candidate_id in enumerate(unique_ids, start=1)
        )
        now = self._clock()
        attempt = ResolutionAttempt(
            attempt_id=uuid4().hex,
            source_message_id=message_id,
            home_id=actor.home_id,
            person_id=actor.person_id,
            original_command=command,
            category=route_decision.category.value,
            action=intent.action,
            target_expression=intent.target_expression,
            parameters=intent.parameters,
            intent_summary=route_decision.intent_summary,
            candidates=tuple(candidates),
            choices=choices,
            choice_candidate_ids=tuple(unique_ids),
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        try:
            await self._active_term_store.save_resolution_attempt(
                attempt,
                message_id,
            )
        except DependencyError as error:
            return self._device_error_response(
                error=error,
                message_id=message_id,
                category=route_decision.category,
                trace=trace,
                started_at=started_at,
            )
        trace.append(
            TraceStep(
                stage="resolve",
                status=CommandStatus.NEEDS_INPUT,
                summary="需要用户确认目标",
            )
        )
        prompt = self._clarification_prompt(intent, choices)
        return self._response(
            message_id=message_id,
            category=route_decision.category,
            route="home_assistant_mcp",
            status=CommandStatus.NEEDS_INPUT,
            message=prompt,
            trace=trace,
            started_at=started_at,
            resolution=ResolutionDetails(
                status=resolution_decision.status,
                confidence=resolution_decision.confidence,
                choices=list(choices),
                attempt_id=attempt.attempt_id,
            ),
        )

    @staticmethod
    def _clarification_prompt(
        intent: DeviceActionIntent,
        choices: tuple[ClarificationChoice, ...],
    ) -> str:
        if not choices:
            return "没有找到可控制的目标，请换一种称呼。"
        quoted_names = [f"“{choice.display_name}”" for choice in choices]
        if len(quoted_names) == 1:
            candidates = quoted_names[0]
        else:
            candidates = "、".join(quoted_names[:-1]) + f"或{quoted_names[-1]}"
        prompt = (
            f"“{intent.target_expression}”可能是{candidates}。"
            "请确认要控制哪一个。"
        )
        if len(choices) > 1:
            prompt += "如果都要控制，也可以说“全部”。"
        return prompt

    async def _match_clarification(
        self,
        command: str,
        message_id: str,
        actor: ActorContext,
        trace: list[TraceStep],
        started_at: float,
    ) -> CommandResponse | None:
        attempt = await self._active_term_store.load_latest_resolution_attempt(
            actor,
            self._clock(),
        )
        if attempt is None:
            return None
        normalized = normalize_term(command)
        choice_index: int | None = None
        for index, choice in enumerate(attempt.choices):
            accepted = {
                normalize_term(choice.choice_id),
                normalize_term(choice.display_name),
                str(index + 1),
            }
            if normalized in accepted:
                choice_index = index
                break
        verification_candidates = list(attempt.candidates)
        selected_all = self._all_clarification_candidate(
            attempt,
            normalized,
        )
        if selected_all is not None:
            candidate_id = selected_all.candidate_id
            verification_candidates.append(selected_all)
        elif choice_index is not None:
            candidate_id = attempt.choice_candidate_ids[choice_index]
        else:
            return None
        resolution = TargetResolutionDecision(
            status=ResolutionStatus.SELECTED,
            selected_candidate_id=candidate_id,
            confidence=1,
            alternative_candidate_ids=(),
            reason="用户明确选择澄清项",
        )
        intent = DeviceActionIntent(
            action=attempt.action,
            target_expression=attempt.target_expression,
            parameters=attempt.parameters,
        )
        try:
            if selected_all is not None:
                await self._active_audit.record(
                    message_id=message_id,
                    event_type="target.clarification_all_selected",
                    service="target_resolution",
                    payload={
                        "source_message_id": attempt.source_message_id,
                        "candidate_ids": attempt.choice_candidate_ids,
                        "entity_ids": list(selected_all.target_entity_ids),
                        "action": attempt.action,
                        "target_expression": attempt.target_expression,
                    },
                    correlation_id=attempt.source_message_id,
                    causation_id=attempt.source_message_id,
                )
            verified = await self._active_verifier.verify(
                decision=resolution,
                candidates=verification_candidates,
                actor=actor,
                intent=intent,
                message_id=message_id,
                correlation_id=attempt.source_message_id,
                causation_id=attempt.source_message_id,
            )
        except (ResolutionError, DependencyError) as error:
            code = getattr(error, "code", "target_verification_failed")
            message = getattr(error, "message", str(error))
            return self._response(
                message_id=message_id,
                category=CommandCategory(attempt.category),
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message=message,
                trace=trace,
                started_at=started_at,
                error_code=code,
            )
        route_decision = RouteDecision(
            category=attempt.category,
            device_command=(
                None
                if attempt.category == CommandCategory.INDIRECT_IOT
                else {
                    "action": attempt.action,
                    "target_expression": attempt.target_expression,
                    "parameters": attempt.parameters,
                }
            ),
            intent_summary=attempt.intent_summary,
            target_expression=(
                attempt.target_expression
                if attempt.category == CommandCategory.INDIRECT_IOT
                else None
            ),
            indirect_action=(
                attempt.action
                if attempt.category == CommandCategory.INDIRECT_IOT
                else None
            ),
        )
        return await self._execute_verified_target(
            command=attempt.original_command,
            decision=route_decision,
            intent=intent,
            verified=verified,
            actor=actor,
            message_id=message_id,
            trace=trace,
            started_at=started_at,
            correlation_id=attempt.source_message_id,
            causation_id=attempt.source_message_id,
        )

    @staticmethod
    def _all_clarification_candidate(
        attempt: ResolutionAttempt,
        normalized_command: str,
    ) -> TargetCandidate | None:
        generic_expressions = {
            "全部",
            "全都",
            "都要",
        }
        action_expressions = {
            "turn_on": {
                "都打开",
                "都开启",
                "全部打开",
                "两盏都开",
                "两盏都打开",
                "两个都打开",
            },
            "turn_off": {
                "都关闭",
                "都关掉",
                "全部关闭",
                "全部关掉",
                "两盏都关",
                "两盏都关闭",
                "两个都关闭",
            },
            "set_brightness": {
                "都调",
                "全部都调",
            },
        }
        accepted_expressions = generic_expressions | action_expressions.get(
            attempt.action,
            set(),
        )
        if (
            normalized_command not in accepted_expressions
            or len(attempt.choice_candidate_ids) < 2
        ):
            return None
        candidate_by_id = {
            candidate.candidate_id: candidate
            for candidate in attempt.candidates
        }
        selected = [
            candidate_by_id[candidate_id]
            for candidate_id in attempt.choice_candidate_ids
            if candidate_id in candidate_by_id
        ]
        if len(selected) != len(attempt.choice_candidate_ids):
            return None
        entity_ids = tuple(
            sorted(
                {
                    entity_id
                    for candidate in selected
                    for entity_id in candidate.target_entity_ids
                }
            )
        )
        catalog_versions = {
            candidate.catalog_version for candidate in selected
        }
        home_ids = {candidate.home_id for candidate in selected}
        if (
            not entity_ids
            or len(entity_ids) > 20
            or len(catalog_versions) != 1
            or len(home_ids) != 1
        ):
            return None
        return TargetCandidate(
            candidate_id="cand_99",
            target_entity_ids=entity_ids,
            display_name="以上全部设备",
            areas=tuple(
                sorted(
                    {
                        area
                        for candidate in selected
                        for area in candidate.areas
                    }
                )
            ),
            domains=tuple(
                sorted(
                    {
                        domain
                        for candidate in selected
                        for domain in candidate.domains
                    }
                )
            ),
            states=tuple(
                state
                for candidate in selected
                for state in candidate.states
            ),
            sources=("clarification_all",),
            matched_terms=(attempt.target_expression,),
            rule_score=0,
            evidence=tuple(
                f"用户确认全部:{candidate.candidate_id}"
                for candidate in selected
            ),
            catalog_version=catalog_versions.pop(),
            home_id=home_ids.pop(),
        )

    @staticmethod
    def _intent_from_route(decision: RouteDecision) -> DeviceActionIntent:
        if decision.category == CommandCategory.DIRECT_IOT:
            if decision.device_command is None:
                raise ValueError("指令路由没有返回完整的设备控制指令。")
            return DeviceActionIntent(
                action=decision.device_command.action,
                target_expression=decision.device_command.target_expression,
                parameters=decision.device_command.parameters,
            )
        if (
            decision.category == CommandCategory.INDIRECT_IOT
            and decision.target_expression
            and decision.indirect_action
        ):
            return DeviceActionIntent(
                action=decision.indirect_action,
                target_expression=decision.target_expression,
            )
        raise ValueError("指令路由没有返回完整的设备控制意图。")

    @staticmethod
    def _strip_target_parameters(
        parameters: dict[str, object],
    ) -> dict[str, object]:
        forbidden = {
            "area",
            "domain",
            "entity_id",
            "floor",
            "name",
            "target",
            "target_expression",
        }
        return {
            key: value
            for key, value in parameters.items()
            if key.casefold() not in forbidden
        }

    @property
    def _active_catalog(self) -> HomeAssistantCatalogProvider:
        assert self._catalog is not None
        return self._catalog

    @property
    def _active_term_store(self) -> TermStoreProtocol:
        assert self._term_store is not None
        return self._term_store

    @property
    def _active_candidate_builder(self) -> CandidateBuilder:
        assert self._candidate_builder is not None
        return self._candidate_builder

    @property
    def _active_verifier(self) -> ResolutionVerifier:
        assert self._verifier is not None
        return self._verifier

    @property
    def _active_audit(self) -> AuditRecorderProtocol:
        assert self._audit is not None
        return self._audit

    async def _execute_direct(
        self,
        decision: RouteDecision,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> CommandResponse:
        if decision.device_command is None:
            return self._response(
                message_id=message_id,
                category=CommandCategory.DIRECT_IOT,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message="指令路由没有返回完整的设备控制指令。",
                trace=trace,
                started_at=started_at,
                error_code="invalid_route_output",
            )
        try:
            tool_call = await self._devices.execute_direct(
                decision.device_command,
                message_id,
                correlation_id,
                causation_id,
            )
        except Exception as error:
            return self._device_error_response(
                error=error,
                message_id=message_id,
                category=CommandCategory.DIRECT_IOT,
                trace=trace,
                started_at=started_at,
            )
        return self._device_success_response(
            message_id=message_id,
            category=CommandCategory.DIRECT_IOT,
            message="Home Assistant 已处理该指令。",
            tool_call=tool_call,
            trace=trace,
            started_at=started_at,
        )

    async def _execute_indirect(
        self,
        command: str,
        decision: RouteDecision,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> CommandResponse:
        if not decision.intent_summary:
            return self._response(
                message_id=message_id,
                category=CommandCategory.INDIRECT_IOT,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message="指令路由没有返回设备控制意图。",
                trace=trace,
                started_at=started_at,
                error_code="invalid_route_output",
            )
        try:
            tools = await self._devices.list_safe_tools(
                message_id,
                correlation_id,
                causation_id,
            )
        except DependencyError as error:
            return self._device_error_response(
                error=error,
                message_id=message_id,
                category=CommandCategory.INDIRECT_IOT,
                trace=trace,
                started_at=started_at,
            )
        try:
            plan_result = await self._codex.plan_device_control(
                command,
                decision.intent_summary,
                tools,
                message_id,
                correlation_id,
                causation_id,
            )
        except DependencyError as error:
            trace.append(
                TraceStep(
                    stage="plan",
                    status="error",
                    summary=error.message,
                )
            )
            return self._response(
                message_id=message_id,
                category=CommandCategory.INDIRECT_IOT,
                route="home_assistant_mcp",
                status=CommandStatus.ERROR,
                message=error.message,
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )
        trace.append(
            TraceStep(
                stage="plan",
                status="success",
                summary="Codex medium 生成设备计划",
            )
        )
        try:
            compatibility_arguments = dict(plan_result.tool_plan.arguments)
            compatibility_arguments["name"] = decision.target_expression
            compatibility_plan = ToolPlan(
                tool_name=plan_result.tool_plan.tool_name,
                arguments=compatibility_arguments,
            )
            tool_call = await self._devices.execute_plan(
                compatibility_plan,
                tools,
                message_id,
                correlation_id,
                causation_id,
            )
        except Exception as error:
            return self._device_error_response(
                error=error,
                message_id=message_id,
                category=CommandCategory.INDIRECT_IOT,
                trace=trace,
                started_at=started_at,
            )
        return self._device_success_response(
            message_id=message_id,
            category=CommandCategory.INDIRECT_IOT,
            message=plan_result.message,
            tool_call=tool_call,
            trace=trace,
            started_at=started_at,
        )

    async def _answer(
        self,
        command: str,
        message_id: str,
        trace: list[TraceStep],
        started_at: float,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> CommandResponse:
        try:
            result = await self._codex.answer(
                command,
                message_id,
                correlation_id,
                causation_id,
            )
        except DependencyError as error:
            trace.append(
                TraceStep(
                    stage="dispatch",
                    status="error",
                    summary=error.message,
                )
            )
            return self._response(
                message_id=message_id,
                category=CommandCategory.OTHER,
                route="codex",
                status=CommandStatus.ERROR,
                message=error.message,
                trace=trace,
                started_at=started_at,
                error_code=error.code,
            )
        trace.append(
            TraceStep(
                stage="dispatch",
                status="success",
                summary="Codex high 普通回答",
            )
        )
        return self._response(
            message_id=message_id,
            category=CommandCategory.OTHER,
            route="codex",
            status=CommandStatus.SUCCESS,
            message=result.message,
            trace=trace,
            started_at=started_at,
        )

    def _device_error_response(
        self,
        *,
        error: Exception,
        message_id: str,
        category: CommandCategory,
        trace: list[TraceStep],
        started_at: float,
    ) -> CommandResponse:
        if isinstance(error, SafetyViolation):
            status = CommandStatus.BLOCKED
            code = error.code
            message = "该工具或目标不在 MVP 的安全执行范围内。"
        elif isinstance(error, (DependencyError, DeviceExecutionError)):
            status = CommandStatus.ERROR
            code = error.code
            message = error.message
        else:
            raise error
        trace.append(
            TraceStep(
                stage="dispatch",
                status=status,
                summary=message,
            )
        )
        return self._response(
            message_id=message_id,
            category=category,
            route="home_assistant_mcp",
            status=status,
            message=message,
            trace=trace,
            started_at=started_at,
            error_code=code,
        )

    def _device_success_response(
        self,
        *,
        message_id: str,
        category: CommandCategory,
        message: str,
        tool_call: ToolCallRecord,
        trace: list[TraceStep],
        started_at: float,
    ) -> CommandResponse:
        trace.extend(
            [
                TraceStep(
                    stage="dispatch",
                    status="success",
                    summary="Home Assistant MCP",
                ),
                TraceStep(
                    stage="result",
                    status="success",
                    summary="工具返回成功",
                ),
            ]
        )
        return self._response(
            message_id=message_id,
            category=category,
            route="home_assistant_mcp",
            status=CommandStatus.SUCCESS,
            message=message,
            trace=trace,
            started_at=started_at,
            tool_call=tool_call,
        )

    @staticmethod
    def _classification_summary(category: CommandCategory) -> str:
        labels = {
            CommandCategory.DIRECT_IOT: "直接 IoT · Codex low",
            CommandCategory.INDIRECT_IOT: "模糊 IoT · Codex low",
            CommandCategory.OTHER: "其他指令 · Codex low",
        }
        return labels[category]

    @staticmethod
    def _response(
        *,
        message_id: str,
        category: CommandCategory,
        route: str,
        status: CommandStatus,
        message: str,
        trace: list[TraceStep],
        started_at: float,
        tool_call: ToolCallRecord | None = None,
        tool_calls: list[ToolCallRecord] | None = None,
        resolution: ResolutionDetails | None = None,
        warnings: list[str] | None = None,
        error_code: str | None = None,
    ) -> CommandResponse:
        return CommandResponse(
            message_id=message_id,
            request_id=message_id,
            category=category,
            route=route,
            status=status,
            message=message,
            tool_call=tool_call,
            tool_calls=tool_calls or [],
            resolution=resolution,
            warnings=warnings or [],
            trace=trace,
            elapsed_ms=max(0, round((perf_counter() - started_at) * 1000)),
            error_code=error_code,
        )

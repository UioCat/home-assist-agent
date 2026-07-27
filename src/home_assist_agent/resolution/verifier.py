from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.errors import DependencyError
from home_assist_agent.ha.catalog import HomeAssistantCatalogProvider
from home_assist_agent.resolution.models import (
    ActorContext,
    DeviceActionIntent,
    ResolutionStatus,
    TargetCandidate,
    TargetResolutionDecision,
    VerifiedTarget,
)


class ResolutionError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ResolutionVerifier:
    def __init__(
        self,
        *,
        catalog: HomeAssistantCatalogProvider,
        audit: AuditRecorderProtocol,
        confidence_threshold: float = 0.80,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self._catalog = catalog
        self._audit = audit
        self._confidence_threshold = confidence_threshold

    async def verify(
        self,
        *,
        decision: TargetResolutionDecision,
        candidates: list[TargetCandidate],
        actor: ActorContext,
        intent: DeviceActionIntent,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> VerifiedTarget:
        selected_candidate_id = decision.selected_candidate_id
        try:
            result = await self._verify(
                decision=decision,
                candidates=candidates,
                actor=actor,
                intent=intent,
                message_id=message_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except ResolutionError as error:
            await self._record_failure(
                message_id=message_id,
                candidate_id=selected_candidate_id,
                error_code=error.code,
                message=error.message,
                retryable=error.retryable,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            raise
        except DependencyError as error:
            await self._record_failure(
                message_id=message_id,
                candidate_id=selected_candidate_id,
                error_code=error.code,
                message=error.message,
                retryable=False,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            raise
        await self._audit.record(
            message_id=message_id,
            event_type="target.verification_succeeded",
            service="target_verifier",
            payload={
                "candidate_id": result.candidate_id,
                "home_id": result.home_id,
                "entity_ids": result.entity_ids,
                "catalog_version": result.catalog_version,
                "action": result.action,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        return result

    async def _verify(
        self,
        *,
        decision: TargetResolutionDecision,
        candidates: list[TargetCandidate],
        actor: ActorContext,
        intent: DeviceActionIntent,
        message_id: str,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> VerifiedTarget:
        if (
            decision.status != ResolutionStatus.SELECTED
            or decision.selected_candidate_id is None
        ):
            raise ResolutionError(
                "target_not_selected",
                "目标解析没有选出单一候选。",
            )
        if decision.confidence < self._confidence_threshold:
            raise ResolutionError(
                "target_low_confidence",
                "目标解析置信度不足。",
            )

        candidate_by_id: dict[str, TargetCandidate] = {}
        for candidate in candidates:
            if candidate.candidate_id in candidate_by_id:
                raise ResolutionError(
                    "target_candidate_set_invalid",
                    "候选集合包含重复编号。",
                )
            candidate_by_id[candidate.candidate_id] = candidate
        selected = candidate_by_id.get(decision.selected_candidate_id)
        if selected is None:
            raise ResolutionError(
                "target_candidate_unknown",
                "目标解析引用了不存在的候选。",
            )
        if selected.home_id != actor.home_id:
            raise ResolutionError(
                "target_outside_home",
                "目标不属于当前家庭。",
            )
        if (
            not selected.target_entity_ids
            or len(selected.target_entity_ids) > 20
            or tuple(sorted(set(selected.target_entity_ids)))
            != selected.target_entity_ids
        ):
            raise ResolutionError(
                "target_entity_set_invalid",
                "目标实体集合无效。",
            )

        refreshed = await self._catalog.snapshot(
            actor,
            message_id,
            correlation_id,
            causation_id,
        )
        if refreshed.home_id != actor.home_id:
            raise ResolutionError(
                "target_outside_home",
                "刷新后的目标目录不属于当前家庭。",
            )
        if refreshed.catalog_version != selected.catalog_version:
            raise ResolutionError(
                "target_catalog_changed",
                "设备目录已变化，需要重新解析目标。",
                retryable=True,
            )

        entity_by_id = {
            entity.entity_id: entity for entity in refreshed.entities
        }
        for entity_id in selected.target_entity_ids:
            entity = entity_by_id.get(entity_id)
            if entity is None:
                raise ResolutionError(
                    "target_not_found",
                    f"目标实体已不存在：{entity_id}",
                )
            if entity.home_id != actor.home_id:
                raise ResolutionError(
                    "target_outside_home",
                    "目标实体不属于当前家庭。",
                )
            if entity.disabled:
                raise ResolutionError(
                    "target_disabled",
                    f"目标实体已禁用：{entity_id}",
                )
            if not entity.available:
                raise ResolutionError(
                    "target_unavailable",
                    f"目标实体当前不可用：{entity_id}",
                )
            if intent.action not in entity.capabilities:
                raise ResolutionError(
                    "target_action_unsupported",
                    f"目标实体不支持当前动作：{entity_id}",
                )

        return VerifiedTarget(
            home_id=actor.home_id,
            candidate_id=selected.candidate_id,
            entity_ids=selected.target_entity_ids,
            catalog_version=refreshed.catalog_version,
            action=intent.action,
        )

    async def _record_failure(
        self,
        *,
        message_id: str,
        candidate_id: str | None,
        error_code: str,
        message: str,
        retryable: bool,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            event_type="target.verification_failed",
            service="target_verifier",
            payload={
                "candidate_id": candidate_id,
                "error": message,
                "retryable": retryable,
            },
            status="error",
            error_code=error_code,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

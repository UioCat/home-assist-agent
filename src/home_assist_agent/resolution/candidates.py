from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from home_assist_agent.resolution.models import (
    ActorContext,
    CatalogSnapshot,
    DeviceActionIntent,
    HaEntitySnapshot,
    TargetCandidate,
    TermScope,
    VisibleTermMapping,
    VisibleTermStatus,
)
from home_assist_agent.resolution.normalize import normalize_term


_SOURCE_WEIGHTS = {
    "personal_provisional": 700.0,
    "personal_approved": 600.0,
    "home_shared": 500.0,
    "ha_alias": 400.0,
    "ha_name": 350.0,
    "area": 250.0,
    "context": 100.0,
    "semantic_fallback": 50.0,
}

_DOMAIN_HINTS = {
    "light": ("灯", "灯光", "照明"),
}


@dataclass(slots=True)
class _CandidateEvidence:
    entities: tuple[HaEntitySnapshot, ...]
    sources: set[str] = field(default_factory=set)
    matched_terms: set[str] = field(default_factory=set)
    evidence: set[str] = field(default_factory=set)
    semantic_score: float = 0

    @property
    def score(self) -> float:
        return (
            sum(_SOURCE_WEIGHTS[source] for source in self.sources)
            + self.semantic_score
        )


class CandidateBuilder:
    def __init__(self, limit: int = 20, target_limit: int = 20) -> None:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        if not 1 <= target_limit <= 20:
            raise ValueError("target_limit must be between 1 and 20")
        self._limit = limit
        self._target_limit = target_limit

    def build(
        self,
        *,
        intent: DeviceActionIntent,
        actor: ActorContext,
        catalog: CatalogSnapshot,
        terms: Sequence[VisibleTermMapping],
        context_terms: Sequence[str] = (),
    ) -> list[TargetCandidate]:
        if catalog.home_id != actor.home_id:
            return []
        expression = normalize_term(intent.target_expression)
        if not expression:
            return []

        eligible = {
            entity.entity_id: entity
            for entity in catalog.entities
            if self._is_eligible(entity, actor, intent)
        }
        collected: dict[tuple[str, ...], _CandidateEvidence] = {}

        for term in terms:
            if not self._term_is_visible(term, actor):
                continue
            if term.normalized_term != expression:
                continue
            entity_ids = tuple(sorted(set(term.target_entity_ids)))
            if (
                not entity_ids
                or len(entity_ids) > self._target_limit
                or any(entity_id not in eligible for entity_id in entity_ids)
            ):
                continue
            source = self._term_source(term)
            self._add(
                collected,
                tuple(eligible[entity_id] for entity_id in entity_ids),
                source,
                term.display_term,
                f"术语映射:{term.mapping_id}",
            )

        for entity in eligible.values():
            aliases = {
                normalize_term(value)
                for value in (*entity.aliases, *entity.device_aliases)
                if normalize_term(value)
            }
            names = {
                normalize_term(value)
                for value in (
                    entity.friendly_name,
                    entity.original_name,
                    entity.device_name,
                )
                if value and normalize_term(value)
            }
            if expression in aliases:
                self._add(
                    collected,
                    (entity,),
                    "ha_alias",
                    intent.target_expression.strip(),
                    f"HA别名:{entity.entity_id}",
                )
            if expression in names:
                self._add(
                    collected,
                    (entity,),
                    "ha_name",
                    intent.target_expression.strip(),
                    f"HA名称:{entity.entity_id}",
                )

        area_entities: dict[str, list[HaEntitySnapshot]] = {}
        for entity in eligible.values():
            if entity.area_name and normalize_term(entity.area_name) == expression:
                area_entities.setdefault(entity.area_name, []).append(entity)
        for area_name, entities in area_entities.items():
            if len(entities) <= self._target_limit:
                self._add(
                    collected,
                    tuple(entities),
                    "area",
                    area_name,
                    f"HA区域:{area_name}",
                )

        normalized_context = {
            normalize_term(value) for value in context_terms if normalize_term(value)
        }
        if expression in normalized_context:
            for evidence in collected.values():
                evidence.sources.add("context")
                evidence.evidence.add("最近澄清上下文")

        if not collected:
            hinted_domains = {
                domain
                for domain, hints in _DOMAIN_HINTS.items()
                if any(hint in expression for hint in hints)
            }
            fallback_entities = (
                entity
                for entity in eligible.values()
                if not hinted_domains or entity.domain in hinted_domains
            )
            for entity in fallback_entities:
                self._add(
                    collected,
                    (entity,),
                    "semantic_fallback",
                    None,
                    f"动作兼容后备候选:{entity.entity_id}",
                    semantic_score=self._semantic_similarity(
                        expression,
                        entity,
                    ),
                )

        ordered = sorted(
            collected.items(),
            key=lambda item: (
                -item[1].score,
                item[0],
            ),
        )[: self._limit]
        return [
            self._to_candidate(
                evidence,
                candidate_id=f"cand_{index:02d}",
                catalog=catalog,
                home_id=actor.home_id,
            )
            for index, (_, evidence) in enumerate(ordered, start=1)
        ]

    @staticmethod
    def _is_eligible(
        entity: HaEntitySnapshot,
        actor: ActorContext,
        intent: DeviceActionIntent,
    ) -> bool:
        return (
            entity.home_id == actor.home_id
            and entity.available
            and not entity.disabled
            and intent.action in entity.capabilities
        )

    @staticmethod
    def _term_is_visible(
        term: VisibleTermMapping,
        actor: ActorContext,
    ) -> bool:
        if term.home_id != actor.home_id:
            return False
        if term.scope == TermScope.HOME:
            return term.status == VisibleTermStatus.APPROVED
        return (
            term.person_id == actor.person_id
            and term.status
            in {VisibleTermStatus.PROVISIONAL, VisibleTermStatus.APPROVED}
        )

    @staticmethod
    def _term_source(term: VisibleTermMapping) -> str:
        if term.scope == TermScope.HOME:
            return "home_shared"
        if term.status == VisibleTermStatus.PROVISIONAL:
            return "personal_provisional"
        return "personal_approved"

    @staticmethod
    def _add(
        collected: dict[tuple[str, ...], _CandidateEvidence],
        entities: tuple[HaEntitySnapshot, ...],
        source: str,
        matched_term: str | None,
        evidence: str,
        semantic_score: float = 0,
    ) -> None:
        ordered_entities = tuple(sorted(entities, key=lambda item: item.entity_id))
        key = tuple(entity.entity_id for entity in ordered_entities)
        item = collected.setdefault(
            key,
            _CandidateEvidence(entities=ordered_entities),
        )
        item.sources.add(source)
        if matched_term is not None:
            item.matched_terms.add(matched_term)
        item.evidence.add(evidence)
        item.semantic_score = max(item.semantic_score, semantic_score)

    @staticmethod
    def _semantic_similarity(
        expression: str,
        entity: HaEntitySnapshot,
    ) -> float:
        names = (
            entity.friendly_name,
            entity.original_name,
            entity.device_name,
            *entity.aliases,
            *entity.device_aliases,
        )
        return max(
            (
                SequenceMatcher(
                    None,
                    expression,
                    normalize_term(name),
                ).ratio()
                * 100
                for name in names
                if name and normalize_term(name)
            ),
            default=0,
        )

    @classmethod
    def _to_candidate(
        cls,
        evidence: _CandidateEvidence,
        *,
        candidate_id: str,
        catalog: CatalogSnapshot,
        home_id: str,
    ) -> TargetCandidate:
        entities = evidence.entities
        names = [cls._entity_display_name(entity) for entity in entities]
        display_name = names[0]
        if len(names) > 1:
            display_name = f"{names[0]} 等 {len(names)} 个设备"
        sources = tuple(
            sorted(
                evidence.sources,
                key=lambda source: (-_SOURCE_WEIGHTS[source], source),
            )
        )
        return TargetCandidate(
            candidate_id=candidate_id,
            target_entity_ids=tuple(entity.entity_id for entity in entities),
            display_name=display_name,
            areas=tuple(
                sorted(
                    {
                        entity.area_name
                        for entity in entities
                        if entity.area_name
                    }
                )
            ),
            domains=tuple(sorted({entity.domain for entity in entities})),
            states=tuple(entity.state for entity in entities),
            sources=sources,
            matched_terms=tuple(sorted(evidence.matched_terms)),
            rule_score=evidence.score,
            evidence=tuple(sorted(evidence.evidence)),
            catalog_version=catalog.catalog_version,
            home_id=home_id,
        )

    @staticmethod
    def _entity_display_name(entity: HaEntitySnapshot) -> str:
        if (
            entity.device_name
            and entity.original_name
            and normalize_term(entity.original_name) in {"灯", "light"}
        ):
            return entity.device_name
        return (
            entity.friendly_name
            or entity.device_name
            or entity.original_name
            or entity.entity_id
        )

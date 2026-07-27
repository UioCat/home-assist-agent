from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ActorContext(ImmutableModel):
    home_id: str = Field(min_length=1, max_length=200)
    person_id: str = Field(min_length=1, max_length=200)


class DeviceActionIntent(ImmutableModel):
    action: Literal["turn_on", "turn_off", "set_brightness"]
    target_expression: str = Field(min_length=1, max_length=200)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_parameters(self) -> "DeviceActionIntent":
        brightness = self.parameters.get("brightness")
        if self.action == "set_brightness":
            if (
                isinstance(brightness, bool)
                or not isinstance(brightness, int)
                or not 0 <= brightness <= 100
            ):
                raise ValueError(
                    "set_brightness requires integer brightness from 0 to 100"
                )
        elif brightness is not None:
            raise ValueError("brightness is only valid for set_brightness")
        return self


class HaEntitySnapshot(ImmutableModel):
    home_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=3)
    domain: str = Field(min_length=1)
    device_id: str | None = None
    area_id: str | None = None
    area_name: str | None = None
    floor_name: str | None = None
    friendly_name: str | None = None
    original_name: str | None = None
    aliases: tuple[str, ...] = ()
    device_name: str | None = None
    device_aliases: tuple[str, ...] = ()
    state: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    capabilities: frozenset[str] = frozenset()
    available: bool = True
    disabled: bool = False

    @model_validator(mode="after")
    def validate_entity_identity(self) -> "HaEntitySnapshot":
        prefix, separator, _ = self.entity_id.partition(".")
        if not separator or prefix != self.domain:
            raise ValueError("entity_id domain must match domain")
        return self


class CatalogSnapshot(ImmutableModel):
    home_id: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    observed_at: datetime
    entities: tuple[HaEntitySnapshot, ...]

    @model_validator(mode="after")
    def validate_home(self) -> "CatalogSnapshot":
        if any(entity.home_id != self.home_id for entity in self.entities):
            raise ValueError("catalog cannot contain entities from another home")
        entity_ids = [entity.entity_id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("catalog entity IDs must be unique")
        return self


class TermScope(StrEnum):
    PERSON = "person"
    HOME = "home"


class VisibleTermStatus(StrEnum):
    PROVISIONAL = "provisional"
    APPROVED = "approved"


class VisibleTermMapping(ImmutableModel):
    mapping_id: str = Field(min_length=1)
    home_id: str = Field(min_length=1)
    scope: TermScope
    person_id: str | None
    display_term: str = Field(min_length=1, max_length=200)
    normalized_term: str = Field(min_length=1, max_length=200)
    target_entity_ids: tuple[str, ...]
    status: VisibleTermStatus

    @model_validator(mode="after")
    def validate_visibility_shape(self) -> "VisibleTermMapping":
        if self.scope == TermScope.PERSON and not self.person_id:
            raise ValueError("personal term requires person_id")
        if self.scope == TermScope.HOME and self.person_id is not None:
            raise ValueError("home term cannot have person_id")
        if (
            self.scope == TermScope.HOME
            and self.status != VisibleTermStatus.APPROVED
        ):
            raise ValueError("home term must be approved")
        if not self.target_entity_ids:
            raise ValueError("term target cannot be empty")
        if len(set(self.target_entity_ids)) != len(self.target_entity_ids):
            raise ValueError("term target entity IDs must be unique")
        return self


class TargetCandidate(ImmutableModel):
    candidate_id: str = Field(pattern=r"^cand_[0-9]{2}$")
    target_entity_ids: tuple[str, ...]
    display_name: str = Field(min_length=1)
    areas: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    sources: tuple[str, ...]
    matched_terms: tuple[str, ...] = ()
    rule_score: float
    evidence: tuple[str, ...] = ()
    catalog_version: str = Field(min_length=1)
    home_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target(self) -> "TargetCandidate":
        if not self.target_entity_ids:
            raise ValueError("candidate target cannot be empty")
        if len(self.target_entity_ids) > 20:
            raise ValueError("candidate target cannot exceed 20 entities")
        if tuple(sorted(set(self.target_entity_ids))) != self.target_entity_ids:
            raise ValueError("candidate target entity IDs must be sorted and unique")
        if not self.sources:
            raise ValueError("candidate requires at least one source")
        return self


class ResolutionStatus(StrEnum):
    SELECTED = "selected"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


class TargetResolutionDecision(ImmutableModel):
    status: ResolutionStatus
    selected_candidate_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    alternative_candidate_ids: tuple[str, ...] = Field(
        default=(),
        max_length=3,
    )
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_selection(self) -> "TargetResolutionDecision":
        if self.status == ResolutionStatus.SELECTED:
            if not self.selected_candidate_id:
                raise ValueError("selected decision requires a candidate")
        elif self.selected_candidate_id is not None:
            raise ValueError("non-selected decision cannot select a candidate")
        return self


class VerifiedTarget(ImmutableModel):
    home_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    entity_ids: tuple[str, ...]
    catalog_version: str = Field(min_length=1)
    action: Literal["turn_on", "turn_off", "set_brightness"]

    @model_validator(mode="after")
    def validate_entity_ids(self) -> "VerifiedTarget":
        if not self.entity_ids or len(self.entity_ids) > 20:
            raise ValueError("verified target must contain 1 to 20 entities")
        if tuple(sorted(set(self.entity_ids))) != self.entity_ids:
            raise ValueError("verified entity IDs must be sorted and unique")
        return self


class ClarificationChoice(ImmutableModel):
    choice_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    area_name: str | None = None
    domain: str = Field(min_length=1)


class RiskPolicyPort(Protocol):
    async def authorize(
        self,
        *,
        actor: ActorContext,
        intent: DeviceActionIntent,
        target: VerifiedTarget,
        message_id: str,
    ) -> None: ...

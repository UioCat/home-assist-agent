from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from home_assist_agent.resolution.models import (
    ClarificationChoice,
    TargetCandidate,
)


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class TermStatus(StrEnum):
    PROVISIONAL = "provisional"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class TermScope(StrEnum):
    PERSON = "person"
    HOME = "home"


class TermMapping(ImmutableModel):
    revision_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    mapping_id: str = Field(min_length=1)
    home_id: str = Field(min_length=1)
    scope: TermScope
    person_id: str | None
    display_term: str = Field(min_length=1, max_length=200)
    normalized_term: str = Field(min_length=1, max_length=200)
    target_entity_ids: tuple[str, ...]
    status: TermStatus
    source_message_id: str = Field(min_length=1)
    source_candidate_id: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    promote_at: datetime | None = None
    updated_at: datetime
    supersedes_mapping_id: str | None = None

    @model_validator(mode="after")
    def validate_mapping(self) -> "TermMapping":
        if self.scope == TermScope.PERSON and not self.person_id:
            raise ValueError("personal term requires person_id")
        if self.scope == TermScope.HOME and self.person_id is not None:
            raise ValueError("home term cannot have person_id")
        if self.scope == TermScope.HOME and self.status == TermStatus.PROVISIONAL:
            raise ValueError("home term cannot be provisional")
        if not self.target_entity_ids:
            raise ValueError("term target cannot be empty")
        if tuple(sorted(set(self.target_entity_ids))) != self.target_entity_ids:
            raise ValueError("term target entity IDs must be sorted and unique")
        if self.status == TermStatus.PROVISIONAL and self.promote_at is None:
            raise ValueError("provisional term requires promote_at")
        if self.status != TermStatus.PROVISIONAL and self.promote_at is not None:
            raise ValueError("only provisional term can have promote_at")
        return self


class ResolutionAttempt(ImmutableModel):
    attempt_id: str = Field(min_length=1)
    source_message_id: str = Field(min_length=1)
    home_id: str = Field(min_length=1)
    person_id: str = Field(min_length=1)
    target_expression: str = Field(min_length=1, max_length=200)
    candidates: tuple[TargetCandidate, ...]
    choices: tuple[ClarificationChoice, ...] = Field(max_length=3)
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_window(self) -> "ResolutionAttempt":
        if self.expires_at <= self.created_at:
            raise ValueError("resolution attempt must expire after creation")
        candidate_ids = {item.candidate_id for item in self.candidates}
        if len(candidate_ids) != len(self.candidates):
            raise ValueError("candidate IDs must be unique")
        return self


class HomePromotionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class HomePromotionRequest(ImmutableModel):
    promotion_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    mapping_id: str = Field(min_length=1)
    home_id: str = Field(min_length=1)
    person_id: str = Field(min_length=1)
    status: HomePromotionStatus
    source_message_id: str = Field(min_length=1)
    created_at: datetime
    expires_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_window(self) -> "HomePromotionRequest":
        if self.expires_at <= self.created_at:
            raise ValueError("promotion request must expire after creation")
        return self

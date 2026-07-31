"""Strict HTTP schemas; callers cannot declare security origin fields."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PropertyWriteRequest(StrictSchema):
    values: dict[str, Any] = Field(min_length=1)


class ServiceInvokeRequest(StrictSchema):
    inputs: dict[str, Any] = Field(default_factory=dict)


class ThingModelImportRequest(StrictSchema):
    name: str = Field(min_length=1)
    source: str = "http"
    tsl: dict[str, Any]


class ConfirmationDecisionRequest(StrictSchema):
    action_hash: str = Field(min_length=1)


class WebhookDecisionRequest(StrictSchema):
    actor: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    confirmation_id: str = Field(min_length=1)
    action_hash: str = Field(min_length=1)

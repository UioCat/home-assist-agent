"""Trusted-origin control policy and immutable action binding."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from iot_mcp.domain.enums import InteractionMode, RiskLevel


class SafeControlError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class TrustedPrincipal(BaseModel):
    """Identity and mode assigned only by a trusted inbound adapter."""

    model_config = ConfigDict(frozen=True)

    actor_id: str
    mode: InteractionMode
    source: Literal["web_session", "admin_token", "machine_token", "mcp", "anonymous"]

    @classmethod
    def web_session(cls, actor_id: str) -> TrustedPrincipal:
        return cls(
            actor_id=actor_id,
            mode=InteractionMode.HUMAN_INTERACTIVE,
            source="web_session",
        )

    @classmethod
    def admin_token(cls, actor_id: str = "admin") -> TrustedPrincipal:
        return cls(
            actor_id=actor_id,
            mode=InteractionMode.AUTONOMOUS,
            source="admin_token",
        )

    @classmethod
    def machine_token(cls, actor_id: str) -> TrustedPrincipal:
        return cls(
            actor_id=actor_id,
            mode=InteractionMode.AUTONOMOUS,
            source="machine_token",
        )

    @classmethod
    def mcp(cls, actor_id: str) -> TrustedPrincipal:
        return cls(actor_id=actor_id, mode=InteractionMode.AUTONOMOUS, source="mcp")

    @classmethod
    def anonymous(cls) -> TrustedPrincipal:
        return cls(actor_id="unknown", mode=InteractionMode.AUTONOMOUS, source="anonymous")


class ControlAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["properties", "service"]
    values: dict[str, Any] = Field(default_factory=dict)
    service: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape(self) -> ControlAction:
        if self.kind == "properties" and (self.service is not None or self.inputs):
            raise ValueError("property actions cannot contain service fields")
        if self.kind == "service" and (self.service is None or self.values):
            raise ValueError("service actions require only service and inputs")
        return self

    @classmethod
    def properties(cls, values: dict[str, Any]) -> ControlAction:
        return cls(kind="properties", values=values)

    @classmethod
    def invoke_service(cls, service: str, inputs: dict[str, Any]) -> ControlAction:
        return cls(kind="service", service=service, inputs=inputs)


class ControlPolicy:
    def requires_confirmation(
        self, *, principal: TrustedPrincipal, risk: RiskLevel
    ) -> bool:
        return (
            principal.mode is InteractionMode.AUTONOMOUS
            and risk is RiskLevel.HIGH
        )


class BoundTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding_id: str
    provider_id: str
    provider_type: str
    external_device_ref: str
    binding_revision: int = Field(ge=1)


def canonical_action_hash(
    device_id: str,
    action: ControlAction,
    *,
    target: BoundTarget | None = None,
    binding_revision: int | None = None,
) -> str:
    if target is None and binding_revision is None:
        raise ValueError("a bound target or binding revision is required")
    payload = {
        "device_id": device_id,
        "target": (
            target.model_dump(mode="json")
            if target is not None
            else {"binding_revision": binding_revision}
        ),
        "action": action.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()

"""Provider boundary for live device discovery, state, control, and events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from iot_mcp.domain.models import utc_now


class ProviderHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    detail: str | None = None


class DeviceState(BaseModel):
    device_ref: str
    values: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)
    freshness: str = "fresh"


class ProviderDevice(BaseModel):
    """A provider-normalized device, including its current presentation snapshot."""

    external_ref: str
    display_name: str
    capability_fingerprint: str
    product_key: str
    product_name: str
    state: DeviceState
    feature_bindings: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    area: str | None = None
    risk_level: str = "low"


class ProviderInventory(BaseModel):
    provider_id: str
    provider_type: str
    devices: list[ProviderDevice] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=utc_now)


class ProviderResult(BaseModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    message: str | None = None


class ProviderEvent(BaseModel):
    device_ref: str
    identifier: str
    values: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class Subscription(Protocol):
    async def wait(self) -> None: ...

    async def close(self) -> None: ...


EventSink = Callable[[ProviderEvent], Awaitable[None] | None]


class DeviceProvider(Protocol):
    provider_id: str
    provider_type: str

    async def health(self) -> ProviderHealth: ...

    async def discover(self) -> ProviderInventory: ...

    async def read_state(
        self, device_ref: str, selectors: list[str] | None = None
    ) -> DeviceState: ...

    async def write_properties(self, device_ref: str, values: dict[str, Any]) -> ProviderResult: ...

    async def invoke_service(
        self, device_ref: str, service: str, inputs: dict[str, Any]
    ) -> ProviderResult: ...

    async def subscribe(self, sink: EventSink) -> Subscription: ...

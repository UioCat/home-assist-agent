from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from httpx import ASGITransport, AsyncClient

from iot_mcp.adapters.outbound.home_assistant.client import HomeAssistantClient
from iot_mcp.adapters.outbound.home_assistant.provider import (
    HomeAssistantDeviceProvider,
)
from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.bootstrap.runtime import build_runtime
from iot_mcp.config.settings import Settings


class OfflineMockProvider(MockDeviceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.write_attempts = 0

    async def read_state(self, device_ref: str, selectors: list[str] | None = None):
        raise ConnectionError("provider is offline")

    async def write_properties(
        self, device_ref: str, values: dict[str, Any]
    ):
        self.write_attempts += 1
        return await super().write_properties(device_ref, values)


class TimeoutMockProvider(MockDeviceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.write_attempts = 0

    async def write_properties(
        self, device_ref: str, values: dict[str, Any]
    ):
        self.write_attempts += 1
        raise TimeoutError("provider timed out")


def _settings(tmp_path: Path, name: str) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / name}",
        machine_tokens={"example-machine-token": "e2e-agent"},
        session_signing_secret="example-session-secret",
        webhook_secret="example-webhook-secret",
    )


async def _device_id(runtime: Any, display_name: str) -> str:
    devices = await runtime.container.devices.list_devices()
    return next(item.device_id for item in devices if item.display_name == display_name)


async def test_provider_offline_and_timeout_never_claim_success(tmp_path: Path) -> None:
    offline = OfflineMockProvider()
    runtime = build_runtime(
        _settings(tmp_path, "offline.db"),
        providers={"mock": offline},
    )
    await runtime.startup()
    try:
        light_id = await _device_id(runtime, "Desk light")
        async with AsyncClient(
            transport=ASGITransport(app=runtime.http_app),
            base_url="https://iot-mcp.test",
        ) as client:
            response = await client.post(
                f"/api/v1/devices/{light_id}/properties:write",
                headers={
                    "Authorization": "Bearer example-machine-token",
                    "Idempotency-Key": "e2e-offline",
                },
                json={"values": {"Brightness": 64}},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "unknown"
        assert response.json()["result"]["code"] == "provider_error"
        assert offline.write_attempts == 0
    finally:
        await runtime.shutdown()

    timeout = TimeoutMockProvider()
    runtime = build_runtime(
        _settings(tmp_path, "timeout.db"),
        providers={"mock": timeout},
    )
    await runtime.startup()
    try:
        light_id = await _device_id(runtime, "Desk light")
        async with AsyncClient(
            transport=ASGITransport(app=runtime.http_app),
            base_url="https://iot-mcp.test",
        ) as client:
            response = await client.post(
                f"/api/v1/devices/{light_id}/properties:write",
                headers={
                    "Authorization": "Bearer example-machine-token",
                    "Idempotency-Key": "e2e-timeout",
                },
                json={"values": {"Brightness": 64}},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "unknown"
        assert response.json()["result"] == {
            "code": "provider_timeout",
            "retryable": True,
        }
        assert timeout.write_attempts == 1
    finally:
        await runtime.shutdown()


async def test_audit_failure_is_fail_closed_before_provider_write(
    tmp_path: Path,
) -> None:
    provider = TimeoutMockProvider()
    runtime = build_runtime(
        _settings(tmp_path, "audit.db"),
        providers={"mock": provider},
    )
    await runtime.startup()
    try:
        light_id = await _device_id(runtime, "Desk light")
        async with runtime.container.engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                CREATE TRIGGER fail_control_operation_insert
                BEFORE INSERT ON control_operations
                BEGIN
                    SELECT RAISE(ABORT, 'audit unavailable');
                END
                """
            )
        async with AsyncClient(
            transport=ASGITransport(app=runtime.http_app),
            base_url="https://iot-mcp.test",
        ) as client:
            response = await client.post(
                f"/api/v1/devices/{light_id}/properties:write",
                headers={
                    "Authorization": "Bearer example-machine-token",
                    "Idempotency-Key": "e2e-audit-failure",
                },
                json={"values": {"Brightness": 65}},
            )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "audit_unavailable"
        assert await runtime.container.operations.list_operations() == []
        assert provider.write_attempts == 0
    finally:
        await runtime.shutdown()


async def test_home_assistant_initial_sync_degrades_without_fake_online_devices(
    tmp_path: Path,
) -> None:
    async def empty_registry() -> dict[str, str | None]:
        return {}

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("home assistant is offline", request=request)

    client = HomeAssistantClient(
        "http://ha.invalid",
        "placeholder-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(offline)),
        registry_loader=empty_registry,
    )
    runtime = build_runtime(
        _settings(tmp_path, "ha-offline.db"),
        providers={"home_assistant": HomeAssistantDeviceProvider(client)},
    )
    await runtime.startup()
    try:
        assert runtime.container.provider_status == {
            "home_assistant": "degraded"
        }
        assert await runtime.container.devices.list_devices() == []
        health = await runtime.container.providers["home_assistant"].health()
        assert health.status == "provider_offline"
    finally:
        await runtime.shutdown()

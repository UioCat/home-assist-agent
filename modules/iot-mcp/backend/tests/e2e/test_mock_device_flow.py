from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.bootstrap.runtime import build_runtime
from iot_mcp.config.settings import Settings
from iot_mcp.domain.models import DeviceInstance, ProviderDeviceBinding


class RecordingMockProvider(MockDeviceProvider):
    def __init__(self) -> None:
        super().__init__()
        self._states["mock:service:e2e"] = {"Level": 0}
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.service_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def write_properties(
        self, device_ref: str, values: dict[str, Any]
    ):
        self.writes.append((device_ref, dict(values)))
        return await super().write_properties(device_ref, values)

    async def invoke_service(
        self, device_ref: str, service: str, inputs: dict[str, Any]
    ):
        self.service_calls.append((device_ref, service, dict(inputs)))
        return await super().invoke_service(device_ref, service, inputs)


async def test_built_console_and_http_surfaces_control_real_mock_state(
    tmp_path: Path,
) -> None:
    web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    provider = RecordingMockProvider()
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'mock-flow.db'}",
            admin_token="example-admin-token",
            machine_tokens={"example-machine-token": "e2e-agent"},
            session_signing_secret="example-session-secret",
            webhook_secret="example-webhook-secret",
            web_dist_path=str(web_dist),
        ),
        providers={"mock": provider},
    )
    await runtime.startup()
    try:
        await runtime.container.devices.upsert_device(
            DeviceInstance(
                device_id="e2e-service-device",
                provider_id="mock",
                display_name="E2E service target",
            )
        )
        await runtime.container.devices.upsert_binding(
            ProviderDeviceBinding(
                device_id="e2e-service-device",
                provider_id="mock",
                provider_type="mock",
                external_device_ref="mock:service:e2e",
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=runtime.http_app),
            base_url="https://iot-mcp.test",
        ) as client:
            index = await client.get("/")
            assert index.status_code == 200
            assert '<div id="root"></div>' in index.text
            asset_path = re.search(r'(?:src|href)="(/assets/[^"]+)"', index.text)
            assert asset_path is not None, "run `npm run build` before the backend E2E suite"
            asset = await client.get(asset_path.group(1))
            assert asset.status_code == 200
            assert asset.content

            deep_route = await client.get("/devices/a-deep-link")
            assert deep_route.status_code == 200
            assert deep_route.text == index.text
            api_miss = await client.get("/api/not-a-route")
            mcp_miss = await client.get("/mcp")
            assert api_miss.status_code == 404
            assert api_miss.json()["error"]["code"] == "not_found"
            assert mcp_miss.status_code == 404
            assert mcp_miss.json()["error"]["code"] == "not_found"

            login = await client.post(
                "/api/v1/auth/session",
                headers={"Authorization": "Bearer example-admin-token"},
            )
            assert login.status_code == 200
            csrf = login.json()["csrf_token"]
            devices = (
                await client.get("/api/v1/devices")
            ).json()
            door = next(item for item in devices if item["display_name"] == "Front door")
            light = next(item for item in devices if item["display_name"] == "Desk light")

            direct = await client.post(
                f"/api/v1/devices/{door['device_id']}/properties:write",
                headers={
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "e2e-human-door",
                },
                json={"values": {"LockState": "UNLOCK"}},
            )
            assert direct.status_code == 200
            assert direct.json()["status"] == "succeeded"
            assert direct.json()["interaction_mode"] == "human_interactive"
            assert (
                await runtime.container.confirmations.get_by_operation(
                    direct.json()["operation_id"]
                )
                is None
            )
            door_state = await client.get(
                f"/api/v1/devices/{door['device_id']}/state"
            )
            assert door_state.json()["values"]["LockState"] == "UNLOCK"

            service = await client.post(
                "/api/v1/devices/e2e-service-device/services/SetLevel:invoke",
                headers={
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "e2e-human-service",
                },
                json={"inputs": {"Level": 7}},
            )
            assert service.status_code == 200
            assert service.json()["status"] == "accepted"
            assert provider.service_calls == [
                ("mock:service:e2e", "SetLevel", {"Level": 7})
            ]
            assert (
                await provider.read_state("mock:service:e2e")
            ).values == {"Level": 7, "service": "SetLevel"}

            machine_headers = {
                "Authorization": "Bearer example-machine-token",
                "Idempotency-Key": "e2e-machine-light",
            }
            first = await client.post(
                f"/api/v1/devices/{light['device_id']}/properties:write",
                headers=machine_headers,
                json={"values": {"Brightness": 61}},
            )
            duplicate = await client.post(
                f"/api/v1/devices/{light['device_id']}/properties:write",
                headers=machine_headers,
                json={"values": {"Brightness": 61}},
            )
            assert first.status_code == duplicate.status_code == 200
            assert first.json()["status"] == "succeeded"
            assert first.json()["interaction_mode"] == "autonomous"
            assert duplicate.json()["operation_id"] == first.json()["operation_id"]
            assert provider.writes == [
                ("mock:lock:front_door", {"LockState": "UNLOCK"}),
                (
                    "mock:service:e2e",
                    {"Level": 7, "service": "SetLevel"},
                ),
                ("mock:light:desk", {"Brightness": 61}),
            ]
            light_state = await client.get(
                f"/api/v1/devices/{light['device_id']}/state"
            )
            assert light_state.json()["values"]["Brightness"] == 61
    finally:
        await runtime.shutdown()

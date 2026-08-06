from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.bootstrap.runtime import build_runtime
from iot_mcp.config.settings import Settings


class RecordingMockProvider(MockDeviceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def write_properties(
        self,
        device_ref: str,
        values: dict[str, Any],
        *,
        message_id: str | None = None,
    ):
        self.writes.append((device_ref, dict(values)))
        return await super().write_properties(
            device_ref, values, message_id=message_id
        )


async def _call_mcp(runtime: Any, name: str, arguments: dict[str, object]) -> dict[str, Any]:
    result = await runtime.mcp_server.call_tool(name, arguments)
    if isinstance(result, tuple):
        return result[1]
    assert len(result) == 1
    return json.loads(result[0].text)


def _signed_headers(secret: str, body: bytes, nonce: str) -> dict[str, str]:
    timestamp = int(time.time())
    canonical = f"{timestamp}.{nonce}.".encode() + body
    signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-IoT-Timestamp": str(timestamp),
        "X-IoT-Nonce": nonce,
        "X-IoT-Signature": f"sha256={signature}",
    }


async def test_mcp_high_risk_waits_for_signed_approval_then_executes_bound_action(
    tmp_path: Path,
) -> None:
    webhook_secret = "example-webhook-secret"
    provider = RecordingMockProvider()
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'confirmation.db'}",
            session_signing_secret="example-session-secret",
            webhook_secret=webhook_secret,
            allowed_confirmation_actors={"owner"},
        ),
        providers={"mock": provider},
    )
    await runtime.startup()
    try:
        devices = await _call_mcp(runtime, "list_devices", {})
        door = next(item for item in devices["data"] if item["display_name"] == "Front door")

        pending = await _call_mcp(
            runtime,
            "set_device_properties",
                {
                    "device_id": door["device_id"],
                    "values": {"LockState": "UNLOCK"},
                    "idempotency_key": "autonomous-door-unlock",
                },
            )
        assert pending["status"] == "pending_confirmation"
        assert pending["confirmation_required"] is True
        assert provider.writes == []
        confirmation = await runtime.container.confirmations.get_by_operation(
            pending["operation_id"]
        )
        assert confirmation is not None
        operation = await runtime.container.operations.get_operation(
            pending["operation_id"]
        )
        assert operation is not None
        assert operation.action == {
            "kind": "properties",
            "values": {"LockState": "UNLOCK"},
            "service": None,
            "inputs": {},
        }

        body = json.dumps(
            {
                "actor": "owner",
                "decision": "approve",
                "confirmation_id": confirmation.confirmation_id,
                "action_hash": confirmation.action_hash,
            },
            separators=(",", ":"),
        ).encode()
        headers = _signed_headers(webhook_secret, body, "e2e-approval-nonce")
        async with AsyncClient(
            transport=ASGITransport(app=runtime.http_app),
            base_url="https://iot-mcp.test",
        ) as client:
            approved = await client.post(
                "/api/v1/message-channels/signed-webhook/callbacks",
                content=body,
                headers=headers,
            )
            replay = await client.post(
                "/api/v1/message-channels/signed-webhook/callbacks",
                content=body,
                headers=headers,
            )

        assert approved.status_code == 200
        assert approved.json()["status"] == "succeeded"
        assert provider.writes == [
            ("mock:lock:front_door", {"LockState": "UNLOCK"})
        ]
        assert (
            await provider.read_state("mock:lock:front_door")
        ).values["LockState"] == "UNLOCK"
        assert replay.status_code == 409
        assert replay.json()["error"]["code"] == "webhook_replay"
        assert len(provider.writes) == 1

        tool_names = {tool.name for tool in await runtime.mcp_server.list_tools()}
        assert not {
            "approve_confirmation",
            "reject_confirmation",
            "decide_confirmation",
        } & tool_names
    finally:
        await runtime.shutdown()

from __future__ import annotations

import json

import pytest

from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.bootstrap.runtime import build_runtime
from iot_mcp.config.settings import Settings


@pytest.fixture
async def runtime(tmp_path):
    application = build_runtime(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}"),
        providers={"mock": MockDeviceProvider()},
    )
    await application.startup()
    try:
        yield application
    finally:
        await application.shutdown()


async def _call(application, name: str, arguments: dict[str, object]) -> dict[str, object]:
    result = await application.mcp_server.call_tool(name, arguments)
    if isinstance(result, tuple):
        return result[1]
    assert len(result) == 1
    return json.loads(result[0].text)


async def test_mcp_registers_only_the_public_tool_contract(runtime) -> None:
    names = {tool.name for tool in await runtime.mcp_server.list_tools()}

    assert names == {
        "list_thing_models",
        "get_thing_model",
        "list_devices",
        "get_device",
        "get_device_state",
        "set_device_properties",
        "invoke_device_service",
        "get_operation",
        "query_device_events",
    }
    assert not {"approve_confirmation", "reject_confirmation", "decide_confirmation"} & names
    write_tools = {
        tool.name: tool
        for tool in await runtime.mcp_server.list_tools()
        if tool.name in {"set_device_properties", "invoke_device_service"}
    }
    for tool in write_tools.values():
        assert not {"interaction_mode", "initiator", "approve"} & set(
            tool.inputSchema["properties"]
        )
        assert "idempotency_key" in tool.inputSchema["properties"]
        assert "idempotency_key" in tool.inputSchema["required"]


async def test_mcp_writes_are_autonomous_and_high_risk_requires_confirmation(runtime) -> None:
    devices = await _call(runtime, "list_devices", {})
    door = next(item for item in devices["data"] if item["display_name"] == "Front door")

    result = await _call(
        runtime,
        "set_device_properties",
        {
            "device_id": door["device_id"],
            "values": {"LockState": "UNLOCK"},
            "idempotency_key": "unlock-front-door",
        },
    )

    assert result["status"] == "pending_confirmation"
    assert result["operation_id"]
    assert result["data"]["confirmation_required"] is True
    operation = await runtime.container.operations.get_operation(result["operation_id"])
    assert operation is not None
    assert operation.interaction_mode == "autonomous"
    assert operation.initiator == "mcp:mcp"
    assert "idempotency_key" not in str(result["data"])


async def test_mcp_caller_stable_idempotency_prevents_duplicate_effects_and_confirmations(
    runtime,
) -> None:
    devices = await _call(runtime, "list_devices", {})
    door = next(item for item in devices["data"] if item["display_name"] == "Front door")
    light = next(item for item in devices["data"] if item["display_name"] == "Desk light")

    high_arguments = {
        "device_id": door["device_id"],
        "values": {"LockState": "UNLOCK"},
        "idempotency_key": "stable-high-risk",
    }
    first_high = await _call(runtime, "set_device_properties", high_arguments)
    duplicate_high = await _call(runtime, "set_device_properties", high_arguments)
    conflict = await _call(
        runtime,
        "set_device_properties",
        {**high_arguments, "values": {"LockState": "LOCK"}},
    )
    low_arguments = {
        "device_id": light["device_id"],
        "values": {"Brightness": 61},
        "idempotency_key": "stable-low-risk",
    }
    first_low = await _call(runtime, "set_device_properties", low_arguments)
    duplicate_low = await _call(runtime, "set_device_properties", low_arguments)

    assert first_high["operation_id"] == duplicate_high["operation_id"]
    assert len(await runtime.container.confirmations.list_requests()) == 1
    assert conflict["status"] == "failed"
    assert conflict["error"]["code"] == "idempotency_conflict"
    assert first_low["operation_id"] == duplicate_low["operation_id"]


async def test_mcp_returns_stable_safe_input_errors(runtime) -> None:
    result = await _call(
        runtime,
        "set_device_properties",
        {
            "device_id": "missing",
            "values": ["not-an-object"],
            "idempotency_key": "invalid-values",
        },
    )

    assert result["status"] == "failed"
    assert result["error"] == {
        "code": "invalid_request",
        "message": "values must be an object",
        "retryable": False,
    }
    assert set(result) == {"request_id", "operation_id", "status", "data", "error", "observed_at"}

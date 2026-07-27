import json
from typing import Any

import pytest

from home_assist_agent.commands.models import (
    CommandResponse,
    ToolDefinition,
    ToolCallRecord,
    ToolExecutionResult,
    ToolPlan,
)
from home_assist_agent.devices.executor import DeviceExecutor
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import (
    DeviceActionIntent,
    VerifiedTarget,
)


def intent(
    expression: str = "床头灯",
    *,
    action: str = "turn_on",
    brightness: int = 40,
) -> DeviceActionIntent:
    return DeviceActionIntent(
        action=action,
        target_expression=expression,
        parameters=(
            {"brightness": brightness} if action == "set_brightness" else {}
        ),
    )


def verified(
    *entity_ids: str,
    action: str = "turn_on",
) -> VerifiedTarget:
    active_ids = tuple(sorted(entity_ids or ("light.bedroom_left",)))
    return VerifiedTarget(
        home_id="home-1",
        candidate_id="cand_01",
        entity_ids=active_ids,
        catalog_version="catalog-v1",
        action=action,
    )


class RecordingMcp:
    def __init__(
        self,
        *,
        fail_entity_id: str | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.fail_entity_id = fail_entity_id
        self.list_error = list_error
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.list_calls = 0

    async def list_tools(
        self,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[ToolDefinition]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return [
            ToolDefinition(
                name="assist.HassTurnOn",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            ToolDefinition(
                name="assist.HassTurnOff",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            ToolDefinition(
                name="assist.HassLightSet",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "brightness": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                    },
                    "required": ["name", "brightness"],
                },
            ),
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ToolExecutionResult:
        self.calls.append((name, arguments, message_id))
        if arguments["name"] == self.fail_entity_id:
            raise DependencyError("ha_tool_failed", "设备执行失败。")
        return ToolExecutionResult(tool_name=name, content="Done")


@pytest.mark.asyncio
async def test_verified_execution_uses_entity_id_not_original_term() -> None:
    mcp = RecordingMcp()
    executor = DeviceExecutor(mcp)

    result = await executor.execute_verified(
        intent=intent("床头灯"),
        target=verified("light.bedroom_left"),
        message_id="message-1",
    )

    assert result.fully_succeeded is True
    assert result.learning_eligible is True
    assert result.tool_calls[0].arguments == {
        "name": "light.bedroom_left",
    }
    assert "床头灯" not in json.dumps(
        result.tool_calls[0].arguments,
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_brightness_is_injected_with_verified_entity_id() -> None:
    mcp = RecordingMcp()
    executor = DeviceExecutor(mcp)

    result = await executor.execute_verified(
        intent=intent(action="set_brightness", brightness=35),
        target=verified("light.left", action="set_brightness"),
        message_id="message-brightness",
    )

    assert result.tool_calls[0].name == "assist.HassLightSet"
    assert result.tool_calls[0].arguments == {
        "name": "light.left",
        "brightness": 35,
    }


@pytest.mark.asyncio
async def test_planner_target_fields_are_removed_before_verified_injection() -> None:
    mcp = RecordingMcp()
    executor = DeviceExecutor(mcp)
    untrusted_plan = ToolPlan(
        tool_name="HassLightSet",
        arguments={
            "name": "light.invented",
            "area": "其他家庭",
            "floor": "二楼",
            "domain": "switch",
            "brightness": 25,
        },
    )

    result = await executor.execute_verified(
        intent=intent(action="set_brightness", brightness=25),
        target=verified("light.left", action="set_brightness"),
        plan=untrusted_plan,
        message_id="message-strip",
    )

    assert result.tool_calls[0].arguments == {
        "brightness": 25,
        "name": "light.left",
    }


@pytest.mark.asyncio
async def test_entity_set_execution_is_ordered_and_fail_fast() -> None:
    mcp = RecordingMcp(fail_entity_id="light.b")
    executor = DeviceExecutor(mcp)

    result = await executor.execute_verified(
        intent=intent(),
        target=verified("light.a", "light.b", "light.c"),
        message_id="message-batch",
    )

    assert result.completed == ("light.a",)
    assert len(result.failed) == 1
    assert result.failed[0].entity_id == "light.b"
    assert result.failed[0].error_code == "ha_tool_failed"
    assert result.skipped == ("light.c",)
    assert result.fully_succeeded is False
    assert result.learning_eligible is False
    assert [call[1]["name"] for call in mcp.calls] == [
        "light.a",
        "light.b",
    ]
    assert [call.arguments["name"] for call in result.tool_calls] == [
        "light.a"
    ]


@pytest.mark.asyncio
async def test_action_mismatch_is_rejected_before_mcp_discovery() -> None:
    mcp = RecordingMcp()
    executor = DeviceExecutor(mcp)

    with pytest.raises(ValueError, match="action"):
        await executor.execute_verified(
            intent=intent(action="turn_on"),
            target=verified("light.left", action="turn_off"),
            message_id="message-mismatch",
        )

    assert mcp.list_calls == 0
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_audit_gated_mcp_failure_prevents_tool_side_effect() -> None:
    mcp = RecordingMcp(
        list_error=DependencyError(
            "audit_unavailable",
            "审计记录写入失败。",
        )
    )
    executor = DeviceExecutor(mcp)

    with pytest.raises(DependencyError) as captured:
        await executor.execute_verified(
            intent=intent(),
            target=verified("light.left"),
            message_id="message-audit-failure",
        )

    assert captured.value.code == "audit_unavailable"
    assert mcp.calls == []


def test_single_tool_call_keeps_compatible_and_plural_response_views() -> None:
    call = ToolCallRecord(
        name="assist.HassTurnOn",
        arguments={"name": "light.left"},
        result="Done",
    )

    response = CommandResponse(
        message_id="message-1",
        request_id="message-1",
        category="direct_iot",
        route="home_assistant_mcp",
        status="success",
        message="完成",
        tool_calls=[call],
    )

    assert response.tool_call == call
    assert response.tool_calls == [call]


def test_multi_entity_response_has_no_misleading_single_call_view() -> None:
    calls = [
        ToolCallRecord(
            name="assist.HassTurnOn",
            arguments={"name": entity_id},
            result="Done",
        )
        for entity_id in ("light.left", "light.right")
    ]

    response = CommandResponse(
        message_id="message-1",
        request_id="message-1",
        category="direct_iot",
        route="home_assistant_mcp",
        status="success",
        message="完成",
        tool_calls=calls,
    )

    assert response.tool_call is None
    assert response.tool_calls == calls

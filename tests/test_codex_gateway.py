import json
from dataclasses import dataclass, field
from pathlib import Path
import sys

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.codex.gateway import (
    CodexGateway,
    ProcessResult,
    SubprocessRunner,
)
from home_assist_agent.commands.models import ToolDefinition
from home_assist_agent.errors import DependencyError


@dataclass
class CapturingRunner:
    output: str
    returncode: int = 0
    stderr: str = ""
    calls: list[dict[str, object]] = field(default_factory=list)

    async def run(
        self,
        args: list[str],
        stdin: str,
        timeout_seconds: float,
    ) -> ProcessResult:
        self.calls.append(
            {
                "args": args,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        output_index = args.index("--output-last-message") + 1
        Path(args[output_index]).write_text(self.output, encoding="utf-8")
        return ProcessResult(
            returncode=self.returncode,
            stdout="codex stdout",
            stderr=self.stderr,
        )


SAFE_TOOL = ToolDefinition(
    name="assist.HassLightSet",
    description="Set a light brightness",
    input_schema={
        "type": "object",
        "properties": {"brightness": {"type": "integer"}},
    },
)


def test_bundled_schemas_require_complete_structured_outputs() -> None:
    schema_dir = (
        Path(__file__).parents[1] / "src" / "home_assist_agent" / "codex" / "schemas"
    )

    route_schema = json.loads(
        (schema_dir / "route_decision.json").read_text(encoding="utf-8")
    )
    plan_schema = json.loads(
        (schema_dir / "device_plan.json").read_text(encoding="utf-8")
    )
    answer_schema = json.loads(
        (schema_dir / "answer_result.json").read_text(encoding="utf-8")
    )

    assert set(route_schema["required"]) == set(route_schema["properties"])
    direct_schema = route_schema["properties"]["device_command"]["anyOf"][0]
    assert set(direct_schema["required"]) == set(direct_schema["properties"])
    assert (
        plan_schema["properties"]["tool_plan"]["properties"]["arguments_json"]["type"]
        == "string"
    )
    assert answer_schema["required"] == ["message"]


@pytest.mark.asyncio
async def test_route_uses_low_reasoning_without_ha_tools() -> None:
    audit = InMemoryAuditRecorder()
    runner = CapturingRunner(
        output=('{"category":"other","device_command":null,"intent_summary":null}')
    )
    gateway = CodexGateway(
        runner=runner,
        codex_binary="/usr/local/bin/codex",
        audit=audit,
    )

    result = await gateway.route("介绍一下你能做什么", "message-route")

    call = runner.calls[0]
    assert result.category == "other"
    assert "model_reasoning_effort=low" in call["args"]
    assert call["timeout_seconds"] == 45
    assert "允许的 Home Assistant MCP 工具" not in call["stdin"]
    assert "只判断用户意图" in call["stdin"]
    events = await audit.list_events("message-route")
    assert events[0].payload["purpose"] == "route"
    assert events[0].payload["reasoning"] == "low"


@pytest.mark.asyncio
async def test_device_plan_uses_medium_reasoning_and_safe_tools() -> None:
    audit = InMemoryAuditRecorder()
    runner = CapturingRunner(
        output=(
            '{"message":"准备调暗客厅灯。","tool_plan":{'
            '"tool_name":"HassLightSet",'
            '"arguments_json":"{\\"name\\":\\"客厅灯\\",'
            '\\"brightness\\":30}"}}'
        )
    )
    gateway = CodexGateway(runner=runner, audit=audit)
    command = '客厅太暗了"; rm -rf /'

    result = await gateway.plan_device_control(
        command,
        "调暗客厅灯",
        [SAFE_TOOL],
        "message-plan",
    )

    call = runner.calls[0]
    assert result.tool_plan.arguments == {
        "name": "客厅灯",
        "brightness": 30,
    }
    assert "model_reasoning_effort=medium" in call["args"]
    assert call["timeout_seconds"] == 90
    assert command not in call["args"]
    assert json.loads(call["stdin"].splitlines()[-3]) == command
    assert "assist.HassLightSet" in call["stdin"]
    assert "该请求已经确认需要控制家庭设备" in call["stdin"]
    events = await audit.list_events("message-plan")
    assert events[0].payload["purpose"] == "device_plan"


@pytest.mark.asyncio
async def test_answer_uses_high_reasoning_and_only_original_user_prompt() -> None:
    audit = InMemoryAuditRecorder()
    runner = CapturingRunner(output='{"message":"我可以帮助你。"}')
    gateway = CodexGateway(runner=runner, audit=audit)

    result = await gateway.answer("介绍一下你能做什么", "message-answer")

    call = runner.calls[0]
    assert result.message == "我可以帮助你。"
    assert "model_reasoning_effort=high" in call["args"]
    assert call["timeout_seconds"] == 150
    assert "用户意图摘要" not in call["stdin"]
    assert "Home Assistant MCP 工具" not in call["stdin"]
    assert json.loads(call["stdin"].splitlines()[-1]) == "介绍一下你能做什么"
    events = await audit.list_events("message-answer")
    assert events[0].payload["purpose"] == "answer"


@pytest.mark.asyncio
async def test_nonzero_codex_exit_records_complete_failure() -> None:
    runner = CapturingRunner(
        output="",
        returncode=1,
        stderr="codex login required",
    )
    audit = InMemoryAuditRecorder()
    gateway = CodexGateway(runner=runner, audit=audit)

    with pytest.raises(DependencyError) as error:
        await gateway.route("你好", "message-failed")

    assert error.value.code == "codex_failed"
    events = await audit.list_events("message-failed")
    assert [event.event_type for event in events] == [
        "codex.request",
        "codex.response",
    ]
    assert events[-1].status == "error"
    assert events[-1].payload == {
        "purpose": "route",
        "returncode": 1,
        "stdout": "codex stdout",
        "stderr": "codex login required",
        "output": None,
        "structured_output": None,
        "error": "本地 Codex 执行失败：codex login required",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        "not json",
        "{}",
        ('{"category":"direct_iot","device_command":null,"intent_summary":null}'),
    ],
)
async def test_invalid_route_output_is_rejected(output: str) -> None:
    gateway = CodexGateway(
        runner=CapturingRunner(output=output),
        audit=InMemoryAuditRecorder(),
    )

    with pytest.raises(DependencyError) as error:
        await gateway.route("打开客厅灯", "message-invalid")

    assert error.value.code == "invalid_route_output"


@pytest.mark.asyncio
async def test_subprocess_timeout_is_reported_and_process_is_stopped() -> None:
    runner = SubprocessRunner()

    with pytest.raises(DependencyError) as error:
        await runner.run(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(5)",
            ],
            stdin="",
            timeout_seconds=0.01,
        )

    assert error.value.code == "codex_timeout"

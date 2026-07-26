import sys
from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

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
            stdout="",
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


def test_bundled_schema_encodes_dynamic_tool_arguments_as_json_string() -> None:
    schema_path = (
        Path(__file__).parents[1]
        / "src"
        / "home_assist_agent"
        / "codex"
        / "schemas"
        / "route_result.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    tool_plan_schema = schema["properties"]["tool_plan"]["anyOf"][0]

    assert set(tool_plan_schema["required"]) == set(
        tool_plan_schema["properties"]
    )
    assert tool_plan_schema["properties"]["arguments_json"]["type"] == "string"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reasoning", "expected_timeout"),
    [("low", 45), ("medium", 90), ("high", 150)],
)
async def test_reasoning_level_maps_to_codex_config_and_timeout(
    tmp_path: Path,
    reasoning: str,
    expected_timeout: int,
) -> None:
    runner = CapturingRunner(
        output='{"category":"other","message":"你好","tool_plan":null}'
    )
    gateway = CodexGateway(
        runner=runner,
        codex_binary="/usr/local/bin/codex",
        schema_path=tmp_path / "route-schema.json",
    )

    result = await gateway.route("你好", reasoning, [SAFE_TOOL])

    call = runner.calls[0]
    assert result.category == "other"
    assert f"model_reasoning_effort={reasoning}" in call["args"]
    assert call["timeout_seconds"] == expected_timeout
    assert call["args"][0:4] == [
        "/usr/local/bin/codex",
        "--ask-for-approval",
        "never",
        "exec",
    ]
    assert "--ignore-user-config" in call["args"]
    assert call["args"][-1] == "-"


@pytest.mark.asyncio
async def test_command_and_safe_tools_are_data_in_stdin_not_shell_arguments(
    tmp_path: Path,
) -> None:
    runner = CapturingRunner(
        output=(
            '{"category":"indirect_iot","message":"准备调暗",'
            '"tool_plan":{"tool_name":"HassLightSet",'
            '"arguments_json":"{\\"name\\":\\"客厅灯\\",\\"brightness\\":30}"}}'
        )
    )
    gateway = CodexGateway(
        runner=runner,
        schema_path=tmp_path / "route-schema.json",
    )
    command = '客厅太暗了"; rm -rf /'

    result = await gateway.route(command, "medium", [SAFE_TOOL])

    call = runner.calls[0]
    assert result.tool_plan is not None
    assert result.tool_plan.arguments == {"name": "客厅灯", "brightness": 30}
    assert command not in call["args"]
    assert json.loads(call["stdin"].splitlines()[-1]) == command
    assert "assist.HassLightSet" in call["stdin"]
    assert "HassBroadcast" not in call["stdin"]


@pytest.mark.asyncio
async def test_nonzero_codex_exit_becomes_dependency_error(tmp_path: Path) -> None:
    runner = CapturingRunner(
        output="",
        returncode=1,
        stderr="codex login required",
    )
    gateway = CodexGateway(
        runner=runner,
        schema_path=tmp_path / "route-schema.json",
    )

    with pytest.raises(DependencyError) as error:
        await gateway.route("你好", "low", [])

    assert error.value.code == "codex_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["not json", "{}", '{"category":"other"}'])
async def test_invalid_codex_output_is_rejected(
    tmp_path: Path,
    output: str,
) -> None:
    runner = CapturingRunner(output=output)
    gateway = CodexGateway(
        runner=runner,
        schema_path=tmp_path / "route-schema.json",
    )

    with pytest.raises(DependencyError) as error:
        await gateway.route("你好", "low", [])

    assert error.value.code == "invalid_codex_output"


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

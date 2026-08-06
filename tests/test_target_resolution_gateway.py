import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from home_assist_agent.audit.recorder import InMemoryAuditRecorder
from home_assist_agent.codex.gateway import CodexGateway, ProcessResult
from home_assist_agent.commands.models import ToolDefinition
from home_assist_agent.commands.models import RouteDecision
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import (
    DeviceActionIntent,
    TargetCandidate,
)


@dataclass
class CapturingRunner:
    output: str
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
            returncode=0,
            stdout="codex stdout",
            stderr=self.stderr,
        )


def candidate(candidate_id: str, entity_id: str) -> TargetCandidate:
    return TargetCandidate(
        candidate_id=candidate_id,
        target_entity_ids=(entity_id,),
        display_name="卧室左侧台灯",
        areas=("卧室",),
        domains=("light",),
        states=("off",),
        sources=("personal_provisional",),
        matched_terms=("床头灯",),
        rule_score=700,
        evidence=("个人短期术语",),
        catalog_version="catalog-v1",
        home_id="home-1",
    )


INTENT = DeviceActionIntent(
    action="turn_on",
    target_expression="床头灯",
)


@pytest.mark.asyncio
async def test_target_resolution_prompt_exposes_only_opaque_candidate_identity() -> None:
    runner = CapturingRunner(
        output=json.dumps(
            {
                "status": "selected",
                "selected_candidate_id": "cand_01",
                "confidence": 0.93,
                "alternative_candidate_ids": [],
                "reason": "与个人术语一致",
            },
            ensure_ascii=False,
        )
    )
    gateway = CodexGateway(
        runner=runner,
        audit=InMemoryAuditRecorder(),
    )

    result = await gateway.resolve_target(
        utterance="打开床头灯",
        action_intent=INTENT,
        candidates=[candidate("cand_01", "light.bedroom_left")],
        message_id="message-1",
    )

    assert result.selected_candidate_id == "cand_01"
    call = runner.calls[0]
    assert '"candidate_id":"cand_01"' in call["stdin"]
    assert "卧室左侧台灯" in call["stdin"]
    assert "light.bedroom_left" not in call["stdin"]
    assert "model_reasoning_effort=medium" in call["args"]


@pytest.mark.asyncio
async def test_semantic_fallback_prompt_requires_reasoning_over_device_facts() -> None:
    runner = CapturingRunner(
        output=json.dumps(
            {
                "status": "no_match",
                "selected_candidate_id": None,
                "confidence": 0,
                "alternative_candidate_ids": [],
                "reason": "没有合理目标",
            },
            ensure_ascii=False,
        )
    )
    gateway = CodexGateway(
        runner=runner,
        audit=InMemoryAuditRecorder(),
    )
    fallback = candidate(
        "cand_01",
        "light.inner",
    ).model_copy(
        update={
            "display_name": "靠内灯",
            "areas": (),
            "sources": ("semantic_fallback",),
            "matched_terms": (),
            "rule_score": 50,
        }
    )

    await gateway.resolve_target(
        utterance="打开床头灯",
        action_intent=INTENT,
        candidates=[fallback],
        message_id="message-semantic-fallback",
    )

    prompt = runner.calls[0]["stdin"]
    assert "semantic_fallback" in prompt
    assert "不能仅因缺少精确字符串匹配就返回 no_match" in prompt
    assert "名称、区域、设备类型" in prompt


def test_target_resolution_schema_has_no_entity_id_output() -> None:
    schema_path = (
        Path(__file__).parents[1]
        / "src"
        / "home_assist_agent"
        / "codex"
        / "schemas"
        / "target_resolution.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "entity_id" not in schema["properties"]
    assert "target_entity_ids" not in schema["properties"]
    assert "uniqueItems" not in schema["properties"][
        "alternative_candidate_ids"
    ]


def test_every_iot_route_carries_a_target_expression() -> None:
    schema_path = (
        Path(__file__).parents[1]
        / "src"
        / "home_assist_agent"
        / "codex"
        / "schemas"
        / "route_decision.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    direct_properties = schema["properties"]["device_command"]["anyOf"][0][
        "properties"
    ]

    assert "target_expression" in direct_properties
    assert "target" not in direct_properties
    assert "target_expression" in schema["required"]
    with pytest.raises(ValueError, match="target_expression"):
        RouteDecision(
            category="indirect_iot",
            intent_summary="调暗灯光",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        {
            "status": "selected",
            "selected_candidate_id": "cand_99",
            "confidence": 0.99,
            "alternative_candidate_ids": [],
            "reason": "候选外编号",
        },
        {
            "status": "selected",
            "selected_candidate_id": "cand_01",
            "confidence": 0.99,
            "alternative_candidate_ids": [],
            "reason": "夹带实体",
            "entity_id": "light.invented",
        },
        {
            "status": "ambiguous",
            "selected_candidate_id": None,
            "confidence": 0.5,
            "alternative_candidate_ids": ["cand_01"],
            "reason": "只有一个歧义项",
        },
        {
            "status": "ambiguous",
            "selected_candidate_id": None,
            "confidence": 0.5,
            "alternative_candidate_ids": ["cand_01", "cand_01"],
            "reason": "重复歧义项",
        },
        {
            "status": "no_match",
            "selected_candidate_id": None,
            "confidence": 0.1,
            "alternative_candidate_ids": ["cand_01"],
            "reason": "无匹配却返回备选",
        },
    ],
)
async def test_invalid_or_inconsistent_target_resolution_is_rejected(
    output: dict[str, object],
) -> None:
    audit = InMemoryAuditRecorder()
    runner = CapturingRunner(
        output=json.dumps(output, ensure_ascii=False),
        stderr="resolver diagnostics",
    )
    gateway = CodexGateway(runner=runner, audit=audit)

    with pytest.raises(DependencyError) as captured:
        await gateway.resolve_target(
            utterance="打开床头灯",
            action_intent=INTENT,
            candidates=[candidate("cand_01", "light.bedroom_left")],
            message_id="message-invalid",
        )

    assert captured.value.code == "invalid_target_resolution_output"
    events = await audit.list_events("message-invalid")
    assert [event.event_type for event in events] == [
        "codex.request",
        "codex.response",
    ]
    assert events[-1].status == "error"
    assert events[-1].payload["stdout"] == "codex stdout"
    assert events[-1].payload["stderr"] == "resolver diagnostics"
    assert events[-1].payload["output"] == json.dumps(
        output,
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_selected_and_alternative_ids_must_all_come_from_candidates() -> None:
    gateway = CodexGateway(
        runner=CapturingRunner(
            output=json.dumps(
                {
                    "status": "selected",
                    "selected_candidate_id": "cand_01",
                    "confidence": 0.88,
                    "alternative_candidate_ids": ["cand_02"],
                    "reason": "主候选匹配",
                },
                ensure_ascii=False,
            )
        ),
        audit=InMemoryAuditRecorder(),
    )

    with pytest.raises(DependencyError) as captured:
        await gateway.resolve_target(
            utterance="打开床头灯",
            action_intent=INTENT,
            candidates=[candidate("cand_01", "light.bedroom_left")],
            message_id="message-alt",
        )

    assert captured.value.code == "invalid_target_resolution_output"


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden_key", ["name", "area", "floor", "domain"])
async def test_indirect_device_plan_rejects_target_fields(
    forbidden_key: str,
) -> None:
    gateway = CodexGateway(
        runner=CapturingRunner(
            output=json.dumps(
                {
                    "message": "准备调节灯光。",
                    "tool_plan": {
                        "tool_name": "HassLightSet",
                        "arguments_json": json.dumps(
                            {forbidden_key: "卧室", "brightness": 30},
                            ensure_ascii=False,
                        ),
                    },
                },
                ensure_ascii=False,
            )
        ),
        audit=InMemoryAuditRecorder(),
    )

    with pytest.raises(DependencyError) as captured:
        await gateway.plan_device_control(
            "卧室太暗了",
            "把目标灯调暗",
            [
                ToolDefinition(
                    name="assist.HassLightSet",
                    input_schema={
                        "type": "object",
                        "properties": {"brightness": {"type": "integer"}},
                    },
                )
            ],
            "message-plan",
        )

    assert captured.value.code == "invalid_device_plan_output"

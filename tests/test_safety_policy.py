import pytest

from home_assist_agent.ha.safety import SafetyPolicy, SafetyViolation


def test_allowed_tool_suffix_resolves_to_live_namespaced_tool() -> None:
    resolved = SafetyPolicy().resolve_tool(
        requested_name="HassTurnOn",
        arguments={"name": "客厅灯"},
        available_tool_names=["assist.HassTurnOn", "assist.HassTurnOff"],
    )

    assert resolved == "assist.HassTurnOn"


def test_tool_outside_allowlist_is_blocked() -> None:
    with pytest.raises(SafetyViolation) as error:
        SafetyPolicy().resolve_tool(
            requested_name="HassBroadcast",
            arguments={"message": "hello"},
            available_tool_names=["assist.HassBroadcast"],
        )

    assert error.value.code == "tool_not_allowed"


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": "前门锁"},
        {"area": "车库", "domain": ["cover"]},
        {"device_class": ["gas"]},
        {"target": {"name": "camera.bedroom"}},
    ],
)
def test_high_risk_target_is_blocked_even_for_allowed_tool(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(SafetyViolation) as error:
        SafetyPolicy().resolve_tool(
            requested_name="HassTurnOff",
            arguments=arguments,
            available_tool_names=["assist.HassTurnOff"],
        )

    assert error.value.code == "unsafe_target"


def test_missing_live_tool_is_not_treated_as_available() -> None:
    with pytest.raises(SafetyViolation) as error:
        SafetyPolicy().resolve_tool(
            requested_name="HassLightSet",
            arguments={"name": "客厅灯", "brightness": 30},
            available_tool_names=["assist.HassTurnOn"],
        )

    assert error.value.code == "tool_unavailable"


def test_only_allowlisted_live_tools_are_exposed_to_codex() -> None:
    filtered = SafetyPolicy().filter_tool_names(
        [
            "assist.HassTurnOn",
            "assist.HassLightSet",
            "assist.HassBroadcast",
            "custom.delete_everything",
        ]
    )

    assert filtered == ["assist.HassTurnOn", "assist.HassLightSet"]

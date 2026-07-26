import pytest

from home_assist_agent.commands.classifier import DirectCommandParser


@pytest.mark.parametrize(
    ("command", "tool_name", "arguments"),
    [
        ("打开客厅灯", "HassTurnOn", {"name": "客厅灯"}),
        ("关闭卧室灯", "HassTurnOff", {"name": "卧室灯"}),
        (
            "把书房灯亮度设置为 30%",
            "HassLightSet",
            {"name": "书房灯", "brightness": 30},
        ),
        (
            "客厅灯调到60%",
            "HassLightSet",
            {"name": "客厅灯", "brightness": 60},
        ),
    ],
)
def test_explicit_commands_compile_to_one_direct_action(
    command: str,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    action = DirectCommandParser().parse(command)

    assert action is not None
    assert action.tool_name == tool_name
    assert action.arguments == arguments


@pytest.mark.parametrize(
    "command",
    [
        "客厅太暗了",
        "把客厅灯调暗一点",
        "我要看电影了",
        "解释什么是 Home Assistant",
    ],
)
def test_commands_requiring_inference_are_not_directly_executed(command: str) -> None:
    assert DirectCommandParser().parse(command) is None


@pytest.mark.parametrize("command", ["", "   ", "打开", "关闭"])
def test_incomplete_commands_are_not_directly_executed(command: str) -> None:
    assert DirectCommandParser().parse(command) is None

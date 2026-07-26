from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class DirectAction:
    tool_name: str
    arguments: dict[str, Any]


class DirectCommandParser:
    _brightness_patterns = (
        re.compile(
            r"^(?:请|帮我)?(?:把)?(?P<target>.+?灯)(?:的)?亮度"
            r"(?:设置|设|调整|调)?(?:为|到)\s*(?P<value>\d{1,3})\s*%?$"
        ),
        re.compile(
            r"^(?:请|帮我)?(?:把)?(?P<target>.+?灯)"
            r"(?:设置|设|调整|调)(?:为|到)\s*(?P<value>\d{1,3})\s*%$"
        ),
    )
    _verb_first_pattern = re.compile(
        r"^(?:请|帮我)?(?P<verb>打开|开启|启动|关闭|关掉|停止)(?P<target>.+)$"
    )
    _target_first_pattern = re.compile(
        r"^(?:请|帮我)?把(?P<target>.+?)(?P<verb>打开|开启|启动|关闭|关掉|停止)$"
    )

    def parse(self, command: str) -> DirectAction | None:
        normalized = re.sub(r"\s+", "", command).strip("。！!，,")
        if not normalized:
            return None

        for pattern in self._brightness_patterns:
            if match := pattern.fullmatch(normalized):
                brightness = int(match.group("value"))
                if 0 <= brightness <= 100:
                    return DirectAction(
                        tool_name="HassLightSet",
                        arguments={
                            "name": match.group("target"),
                            "brightness": brightness,
                        },
                    )
                return None

        for pattern in (self._verb_first_pattern, self._target_first_pattern):
            if match := pattern.fullmatch(normalized):
                tool_name = (
                    "HassTurnOn"
                    if match.group("verb") in {"打开", "开启", "启动"}
                    else "HassTurnOff"
                )
                return DirectAction(
                    tool_name=tool_name,
                    arguments={"name": match.group("target")},
                )

        return None

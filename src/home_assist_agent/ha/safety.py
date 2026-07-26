from typing import Any


class SafetyViolation(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SafetyPolicy:
    _allowed_tool_suffixes = frozenset(
        {
            "HassTurnOn",
            "HassTurnOff",
            "HassLightSet",
            "GetLiveContext",
        }
    )
    _unsafe_tokens = frozenset(
        {
            "lock",
            "door",
            "garage",
            "gas",
            "water",
            "camera",
            "门锁",
            "门禁",
            "车库",
            "燃气",
            "水阀",
            "摄像头",
        }
    )

    def resolve_tool(
        self,
        requested_name: str,
        arguments: dict[str, Any],
        available_tool_names: list[str],
    ) -> str:
        requested_suffix = self._suffix(requested_name)
        if requested_suffix not in self._allowed_tool_suffixes:
            raise SafetyViolation("tool_not_allowed")

        if self._contains_unsafe_target(arguments):
            raise SafetyViolation("unsafe_target")

        if requested_name in available_tool_names:
            return requested_name

        for available_name in available_tool_names:
            if self._suffix(available_name) == requested_suffix:
                return available_name

        raise SafetyViolation("tool_unavailable")

    def filter_tool_names(self, tool_names: list[str]) -> list[str]:
        return [
            name
            for name in tool_names
            if self._suffix(name) in self._allowed_tool_suffixes
        ]

    @staticmethod
    def _suffix(tool_name: str) -> str:
        return tool_name.rsplit(".", maxsplit=1)[-1]

    def _contains_unsafe_target(self, value: Any) -> bool:
        if isinstance(value, str):
            normalized = value.casefold()
            return any(token in normalized for token in self._unsafe_tokens)
        if isinstance(value, dict):
            return any(self._contains_unsafe_target(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(self._contains_unsafe_target(item) for item in value)
        return False

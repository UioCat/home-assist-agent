"""Port for structured confirmation, result, and alert delivery."""

from __future__ import annotations

from typing import Any, Protocol


class MessageChannel(Protocol):
    async def send_confirmation(self, payload: dict[str, Any]) -> None: ...

    async def send_result(self, payload: dict[str, Any]) -> None: ...

    async def send_alert(self, payload: dict[str, Any]) -> None: ...

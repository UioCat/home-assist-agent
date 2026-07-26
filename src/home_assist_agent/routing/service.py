from typing import Protocol

from home_assist_agent.commands.models import RouteDecision


class CodexRouterProtocol(Protocol):
    async def route(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> RouteDecision: ...


class InstructionRouter:
    def __init__(self, codex: CodexRouterProtocol) -> None:
        self._codex = codex

    async def route(
        self,
        command: str,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> RouteDecision:
        return await self._codex.route(
            command,
            message_id,
            correlation_id,
            causation_id,
        )

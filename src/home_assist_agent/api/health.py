from collections.abc import Callable
import shutil
from typing import Any

from home_assist_agent.api.models import (
    CodexHealth,
    HaMcpHealth,
    HealthResponse,
)
from home_assist_agent.errors import DependencyError


class CodexHealthProbe:
    def __init__(
        self,
        binary: str,
        runner: Any,
        binary_resolver: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._binary = binary
        self._runner = runner
        self._binary_resolver = binary_resolver

    async def check(self) -> tuple[bool, bool, str | None]:
        binary_path = self._binary_resolver(self._binary)
        if binary_path is None:
            return False, False, "codex_not_found"
        try:
            result = await self._runner.run(
                [binary_path, "login", "status"],
                stdin="",
                timeout_seconds=5,
            )
        except DependencyError as error:
            return True, False, error.code
        if result.returncode == 0:
            return True, True, None
        return True, False, "codex_not_authenticated"


class HealthService:
    def __init__(
        self,
        codex_probe: Any,
        ha_mcp: Any,
        ha_configured: bool,
    ) -> None:
        self._codex_probe = codex_probe
        self._ha_mcp = ha_mcp
        self._ha_configured = ha_configured

    async def snapshot(self) -> HealthResponse:
        installed, authenticated, codex_error = await self._codex_probe.check()
        if not self._ha_configured:
            ha_health = HaMcpHealth(
                configured=False,
                connected=False,
                tool_count=0,
                error_code="ha_not_configured",
            )
        else:
            try:
                tools = await self._ha_mcp.list_tools()
                ha_health = HaMcpHealth(
                    configured=True,
                    connected=True,
                    tool_count=len(tools),
                )
            except DependencyError as error:
                ha_health = HaMcpHealth(
                    configured=True,
                    connected=False,
                    tool_count=0,
                    error_code=error.code,
                )

        return HealthResponse(
            backend="online",
            codex=CodexHealth(
                installed=installed,
                authenticated=authenticated,
                error_code=codex_error,
            ),
            ha_mcp=ha_health,
        )

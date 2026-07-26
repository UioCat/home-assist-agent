from fastapi import FastAPI

from home_assist_agent.api.app import create_app
from home_assist_agent.api.health import CodexHealthProbe, HealthService
from home_assist_agent.codex.gateway import CodexGateway, SubprocessRunner
from home_assist_agent.commands.service import CommandService
from home_assist_agent.ha.mcp_client import HomeAssistantMcpClient
from home_assist_agent.settings import AppSettings


def build_app(settings: AppSettings | None = None) -> FastAPI:
    active_settings = settings or AppSettings()
    token = (
        active_settings.ha_token.get_secret_value()
        if active_settings.ha_token is not None
        else None
    )
    ha_mcp = HomeAssistantMcpClient(
        url=active_settings.ha_mcp_url,
        token=token,
        timeout_seconds=active_settings.ha_mcp_timeout_seconds,
    )
    runner = SubprocessRunner()
    codex = CodexGateway(
        runner=runner,
        codex_binary=active_settings.codex_binary,
    )
    command_service = CommandService(codex=codex, ha_mcp=ha_mcp)
    health_service = HealthService(
        codex_probe=CodexHealthProbe(
            binary=active_settings.codex_binary,
            runner=runner,
        ),
        ha_mcp=ha_mcp,
        ha_configured=token is not None,
    )
    return create_app(
        command_service=command_service,
        health_service=health_service,
        frontend_dist=active_settings.frontend_dist,
    )

from fastapi import FastAPI

from home_assist_agent.api.app import create_app
from home_assist_agent.api.health import CodexHealthProbe, HealthService
from home_assist_agent.automation.rules import NoopAutomationRuleEngine
from home_assist_agent.audit.recorder import SQLiteAuditRecorder
from home_assist_agent.channels.message import MessageChannel
from home_assist_agent.codex.gateway import CodexGateway, SubprocessRunner
from home_assist_agent.commands.service import CommandOrchestrator
from home_assist_agent.context.store import SQLiteHouseholdContextStore
from home_assist_agent.devices.executor import DeviceExecutor
from home_assist_agent.events.service import EventService
from home_assist_agent.events.store import SQLiteEventReceiptStore
from home_assist_agent.ha.mcp_client import HomeAssistantMcpClient
from home_assist_agent.routing.service import InstructionRouter
from home_assist_agent.settings import AppSettings


def build_app(settings: AppSettings | None = None) -> FastAPI:
    active_settings = settings or AppSettings()
    audit = SQLiteAuditRecorder(active_settings.audit_db_path)
    token = (
        active_settings.ha_token.get_secret_value()
        if active_settings.ha_token is not None
        else None
    )
    ha_mcp = HomeAssistantMcpClient(
        url=active_settings.ha_mcp_url,
        token=token,
        timeout_seconds=active_settings.ha_mcp_timeout_seconds,
        audit=audit,
    )
    runner = SubprocessRunner()
    codex = CodexGateway(
        runner=runner,
        codex_binary=active_settings.codex_binary,
        audit=audit,
    )
    command_orchestrator = CommandOrchestrator(
        router=InstructionRouter(codex),
        codex=codex,
        devices=DeviceExecutor(ha_mcp),
    )
    command_service = MessageChannel(
        orchestrator=command_orchestrator,
        audit=audit,
    )
    event_service = EventService(
        receipts=SQLiteEventReceiptStore(active_settings.event_db_path),
        context=SQLiteHouseholdContextStore(active_settings.event_db_path),
        rules=NoopAutomationRuleEngine(),
        commands=command_orchestrator,
        audit=audit,
    )
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
        audit_query=audit,
        event_service=event_service,
        frontend_dist=active_settings.frontend_dist,
    )

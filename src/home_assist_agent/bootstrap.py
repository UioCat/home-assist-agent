from fastapi import FastAPI

from home_assist_agent.api.app import create_app
from home_assist_agent.api.health import CodexHealthProbe, HealthService
from home_assist_agent.automation.rules import NoopAutomationRuleEngine
from home_assist_agent.audit.recorder import SQLiteAuditRecorder
from home_assist_agent.channels.message import MessageChannel
from home_assist_agent.codex.gateway import CodexGateway, SubprocessRunner
from home_assist_agent.commands.service import CommandOrchestrator
from home_assist_agent.conversations.coordinator import ConversationCoordinator
from home_assist_agent.conversations.store import SQLiteConversationStore
from home_assist_agent.context.store import SQLiteHouseholdContextStore
from home_assist_agent.devices.executor import DeviceExecutor
from home_assist_agent.events.service import EventService
from home_assist_agent.events.store import SQLiteEventReceiptStore
from home_assist_agent.ha.mcp_client import HomeAssistantMcpClient
from home_assist_agent.ha.catalog import HomeAssistantCatalogClient
from home_assist_agent.resolution.candidates import CandidateBuilder
from home_assist_agent.resolution.models import ActorContext
from home_assist_agent.resolution.verifier import ResolutionVerifier
from home_assist_agent.routing.service import InstructionRouter
from home_assist_agent.settings import AppSettings
from home_assist_agent.terms.service import (
    DeterministicCorrectionResolver,
    TermLearningService,
)
from home_assist_agent.terms.store import SQLiteTermStore
from home_assist_agent.terms.worker import TermPromotionWorker


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
    actor = ActorContext(
        home_id=active_settings.home_id,
        person_id=active_settings.person_id,
    )
    catalog = None
    term_store = None
    candidate_builder = None
    verifier = None
    term_learning = None
    promotion_worker = None
    if active_settings.target_resolution_enabled:
        catalog = HomeAssistantCatalogClient(
            base_url=active_settings.ha_base_url,
            token=token,
            timeout_seconds=active_settings.ha_catalog_timeout_seconds,
            audit=audit,
        )
        term_store = SQLiteTermStore(
            active_settings.term_db_path,
            audit=audit,
            provisional_seconds=active_settings.term_provisional_seconds,
        )
        candidate_builder = CandidateBuilder(
            limit=active_settings.target_candidate_limit,
        )
        verifier = ResolutionVerifier(
            catalog=catalog,
            audit=audit,
            confidence_threshold=(
                active_settings.target_resolution_confidence
            ),
        )
        correction_resolver = DeterministicCorrectionResolver(
            catalog=catalog,
            term_store=term_store,
            candidate_builder=candidate_builder,
            codex=codex,
            verifier=verifier,
            audit=audit,
        )
        term_learning = TermLearningService(
            store=term_store,
            audit=audit,
            correction_resolver=correction_resolver,
        )
        promotion_worker = TermPromotionWorker(
            store=term_store,
            audit=audit,
        )
    command_orchestrator = CommandOrchestrator(
        router=InstructionRouter(codex),
        codex=codex,
        devices=DeviceExecutor(ha_mcp),
        catalog=catalog,
        term_store=term_store,
        candidate_builder=candidate_builder,
        verifier=verifier,
        audit=audit,
        target_resolution_enabled=(
            active_settings.target_resolution_enabled
        ),
        term_learning=term_learning,
    )
    command_service = MessageChannel(
        orchestrator=command_orchestrator,
        audit=audit,
        actor=actor,
        conversations=ConversationCoordinator(
            store=SQLiteConversationStore(
                active_settings.conversation_db_path
            ),
            session=codex,
            audit=audit,
        ),
    )
    event_service = EventService(
        receipts=SQLiteEventReceiptStore(active_settings.event_db_path),
        context=SQLiteHouseholdContextStore(active_settings.event_db_path),
        rules=NoopAutomationRuleEngine(),
        commands=command_orchestrator,
        audit=audit,
        actor=actor,
    )
    health_service = HealthService(
        codex_probe=CodexHealthProbe(
            binary=active_settings.codex_binary,
            runner=runner,
        ),
        ha_mcp=ha_mcp,
        ha_configured=token is not None,
    )
    app = create_app(
        command_service=command_service,
        health_service=health_service,
        audit_query=audit,
        event_service=event_service,
        frontend_dist=active_settings.frontend_dist,
        promotion_worker=promotion_worker,
        cors_origins=active_settings.cors_origins,
    )
    app.state.target_resolution_enabled = (
        active_settings.target_resolution_enabled
    )
    return app

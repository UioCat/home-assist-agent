"""One composition root shared by the HTTP and MCP entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from iot_mcp.adapters.outbound.home_assistant.client import HomeAssistantClient
from iot_mcp.adapters.outbound.home_assistant.provider import HomeAssistantDeviceProvider
from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.adapters.outbound.persistence.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from iot_mcp.adapters.outbound.persistence.repositories import (
    ConfirmationRepository,
    DeviceRepository,
    OperationRepository,
    StateRepository,
    ThingModelRepository,
    WebhookNonceRepository,
)
from iot_mcp.adapters.outbound.webhook.channel import SignedWebhookMessageChannel
from iot_mcp.application.confirmation_service import ConfirmationService
from iot_mcp.application.control_service import ControlService
from iot_mcp.application.query_service import QueryService
from iot_mcp.application.sync_service import DeviceSyncService
from iot_mcp.config.settings import Settings
from iot_mcp.ports.device_provider import DeviceProvider


@dataclass(slots=True)
class ApplicationContainer:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    providers: dict[str, DeviceProvider]
    models: ThingModelRepository
    devices: DeviceRepository
    states: StateRepository
    operations: OperationRepository
    confirmations: ConfirmationRepository
    webhook_nonces: WebhookNonceRepository
    webhook_channel: SignedWebhookMessageChannel
    control: ControlService
    confirmation_service: ConfirmationService
    queries: QueryService
    provider_status: dict[str, str] = field(default_factory=dict)
    _started: bool = False

    async def startup(self) -> None:
        if self._started:
            return
        await initialize_database(self.engine)
        for provider_id, provider in self.providers.items():
            try:
                await DeviceSyncService(provider, self.models, self.devices, self.states).sync()
            except Exception:
                # A failed sync is explicitly degraded; no inventory or state is invented.
                self.provider_status[provider_id] = "degraded"
            else:
                self.provider_status[provider_id] = "healthy"
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        for provider in self.providers.values():
            close = getattr(provider, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass
        await self.engine.dispose()
        self._started = False


def create_container(
    settings: Settings | None = None,
    *,
    providers: dict[str, DeviceProvider] | None = None,
) -> ApplicationContainer:
    settings = settings or Settings()
    resolved_providers = providers if providers is not None else _providers_from_settings(settings)
    engine = create_database_engine(settings.database_url, echo=settings.sqlite_echo)
    sessions = create_session_factory(engine)
    models = ThingModelRepository(sessions)
    devices = DeviceRepository(sessions)
    operations = OperationRepository(sessions)
    confirmations = ConfirmationRepository(sessions)
    webhook_nonces = WebhookNonceRepository(sessions)
    webhook_channel = SignedWebhookMessageChannel(
        secret=settings.webhook_secret,
        allowed_actor_ids=settings.allowed_confirmation_actors,
        nonces=webhook_nonces,
        timestamp_tolerance_seconds=settings.webhook_timestamp_tolerance_seconds,
        send_url=settings.webhook_send_url,
    )
    control = ControlService(
        devices=devices,
        operations=operations,
        confirmations=confirmations,
        providers=resolved_providers,
        confirmation_actor=sorted(settings.allowed_confirmation_actors)[0],
        models=models,
        message_channel=webhook_channel,
        confirmation_ttl_seconds=settings.confirmation_ttl_seconds,
    )
    confirmation_service = ConfirmationService(
        devices=devices,
        operations=operations,
        confirmations=confirmations,
        control=control,
    )
    control.bind_confirmation_service(confirmation_service)
    return ApplicationContainer(
        settings=settings,
        engine=engine,
        sessions=sessions,
        providers=resolved_providers,
        models=models,
        devices=devices,
        states=StateRepository(sessions),
        operations=operations,
        confirmations=confirmations,
        webhook_nonces=webhook_nonces,
        webhook_channel=webhook_channel,
        control=control,
        confirmation_service=confirmation_service,
        queries=QueryService(
            models=models,
            devices=devices,
            operations=operations,
            providers=resolved_providers,
        ),
    )


def _providers_from_settings(settings: Settings) -> dict[str, DeviceProvider]:
    providers: dict[str, DeviceProvider] = {}
    if settings.mock_provider_enabled:
        providers[MockDeviceProvider.provider_id] = MockDeviceProvider()
    if settings.home_assistant_url and settings.home_assistant_token:
        client = HomeAssistantClient(
            settings.home_assistant_url,
            settings.home_assistant_token,
            timeout_seconds=settings.home_assistant_timeout_seconds,
        )
        providers[HomeAssistantDeviceProvider.provider_id] = HomeAssistantDeviceProvider(client)
    if not providers:
        providers[MockDeviceProvider.provider_id] = MockDeviceProvider()
    return providers

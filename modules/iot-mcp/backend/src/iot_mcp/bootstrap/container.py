"""One composition root shared by the HTTP and MCP entrypoints."""

from __future__ import annotations

import asyncio
import inspect
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
from iot_mcp.domain.enums import Freshness
from iot_mcp.domain.models import DeviceEvent, PropertySnapshot
from iot_mcp.ports.device_provider import (
    DeviceProvider,
    ProviderEvent,
    Subscription,
)


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
    _last_sync_success: dict[str, bool] = field(default_factory=dict)
    _background_tasks: dict[str, asyncio.Task[None]] = field(
        default_factory=dict
    )
    _subscriptions: dict[str, Subscription] = field(default_factory=dict)
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
                self._last_sync_success[provider_id] = False
            else:
                self.provider_status[provider_id] = "healthy"
                self._last_sync_success[provider_id] = True
        self._started = True
        for provider_id, provider in self.providers.items():
            self._background_tasks[f"subscribe:{provider_id}"] = (
                asyncio.create_task(
                    self._subscription_loop(provider_id, provider)
                )
            )
        self._background_tasks["reconcile"] = asyncio.create_task(
            self._reconciliation_loop()
        )

    async def shutdown(self) -> None:
        if not self._started:
            return
        self._started = False
        subscriptions = list(self._subscriptions.values())
        self._subscriptions.clear()
        for subscription in subscriptions:
            try:
                await subscription.close()
            except Exception:
                pass
        tasks = list(self._background_tasks.values())
        self._background_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for provider in self.providers.values():
            close = getattr(provider, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass
        await self.engine.dispose()

    async def _subscription_loop(
        self, provider_id: str, provider: DeviceProvider
    ) -> None:
        while self._started:
            subscription: Subscription | None = None
            try:
                subscription = await provider.subscribe(
                    lambda event: self._persist_provider_event(
                        provider_id, provider, event
                    )
                )
                self._subscriptions[provider_id] = subscription
                if self._last_sync_success.get(provider_id):
                    self.provider_status[provider_id] = "healthy"
                wait = getattr(subscription, "wait", None)
                if wait is None:
                    while self._started:
                        await asyncio.sleep(
                            self.settings.provider_reconnect_delay_seconds
                        )
                else:
                    result = wait()
                    if inspect.isawaitable(result):
                        await result
                if self._started:
                    raise RuntimeError("provider subscription ended")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.provider_status[provider_id] = "degraded"
                await asyncio.sleep(
                    self.settings.provider_reconnect_delay_seconds
                )
            finally:
                if self._subscriptions.get(provider_id) is subscription:
                    self._subscriptions.pop(provider_id, None)
                if subscription is not None:
                    try:
                        await subscription.close()
                    except Exception:
                        pass

    async def _reconciliation_loop(self) -> None:
        while self._started:
            try:
                await asyncio.sleep(
                    self.settings.reconcile_interval_seconds
                )
            except asyncio.CancelledError:
                raise
            if not self._started:
                return
            for provider_id, provider in self.providers.items():
                try:
                    await DeviceSyncService(
                        provider,
                        self.models,
                        self.devices,
                        self.states,
                    ).sync()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._last_sync_success[provider_id] = False
                    self.provider_status[provider_id] = "degraded"
                else:
                    self._last_sync_success[provider_id] = True
                    self.provider_status[provider_id] = "healthy"

    async def _persist_provider_event(
        self,
        provider_id: str,
        provider: DeviceProvider,
        event: ProviderEvent,
    ) -> None:
        binding = await self.devices.get_binding_by_provider_ref(
            provider_id,
            provider.provider_type,
            event.device_ref,
        )
        if binding is None:
            self.provider_status[provider_id] = "degraded"
            return
        for identifier, value in event.values.items():
            await self.states.add_snapshot(
                PropertySnapshot(
                    device_id=binding.device_id,
                    identifier=identifier,
                    value=value,
                    observed_at=event.occurred_at,
                    source=provider.provider_type,
                    freshness=Freshness.FRESH,
                )
            )
        await self.states.add_event(
            DeviceEvent(
                device_id=binding.device_id,
                identifier=event.identifier,
                type="info",
                output_data=event.values,
                occurred_at=event.occurred_at,
                source=provider.provider_type,
            )
        )


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

"""FastAPI composition root for the standalone IoT MCP backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from iot_mcp.adapters.inbound.http.routes import router
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
)
from iot_mcp.adapters.outbound.webhook.channel import SignedWebhookMessageChannel
from iot_mcp.application.confirmation_service import ConfirmationService
from iot_mcp.application.control_service import ControlService
from iot_mcp.application.policy import SafeControlError
from iot_mcp.application.query_service import QueryService
from iot_mcp.config.settings import Settings
from iot_mcp.ports.device_provider import DeviceProvider


def create_app(
    *,
    settings: Settings | None = None,
    providers: dict[str, DeviceProvider] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    providers = providers or {"mock": MockDeviceProvider()}
    engine = create_database_engine(settings.database_url, echo=settings.sqlite_echo)
    sessions = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await initialize_database(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="IoT MCP", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.providers = providers
    app.state.models = ThingModelRepository(sessions)
    app.state.devices = DeviceRepository(sessions)
    app.state.states = StateRepository(sessions)
    app.state.operations = OperationRepository(sessions)
    app.state.confirmations = ConfirmationRepository(sessions)
    app.state.webhook_channel = SignedWebhookMessageChannel(
        secret=settings.webhook_secret,
        allowed_actor_ids=settings.allowed_confirmation_actors,
        timestamp_tolerance_seconds=settings.webhook_timestamp_tolerance_seconds,
        nonce_ttl_seconds=settings.webhook_nonce_ttl_seconds,
        send_url=settings.webhook_send_url,
    )
    confirmation_actor = sorted(settings.allowed_confirmation_actors)[0]
    app.state.control = ControlService(
        devices=app.state.devices,
        operations=app.state.operations,
        confirmations=app.state.confirmations,
        providers=providers,
        confirmation_actor=confirmation_actor,
        models=app.state.models,
        message_channel=app.state.webhook_channel,
        confirmation_ttl_seconds=settings.confirmation_ttl_seconds,
    )
    app.state.confirmation_service = ConfirmationService(
        devices=app.state.devices,
        operations=app.state.operations,
        confirmations=app.state.confirmations,
        control=app.state.control,
    )
    app.state.control.bind_confirmation_service(app.state.confirmation_service)
    app.state.queries = QueryService(
        models=app.state.models,
        devices=app.state.devices,
        operations=app.state.operations,
        providers=providers,
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(SafeControlError)
    async def safe_control_error_handler(
        request: Request, error: SafeControlError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
        )

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, _: Exception) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code="invalid_request",
            message="request validation failed",
            retryable=False,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _: Exception) -> JSONResponse:
        return _error_response(
            request,
            status_code=500,
            code="internal_error",
            message="an internal error occurred",
            retryable=False,
        )

    app.include_router(router)
    return app


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id},
    )


app = create_app()

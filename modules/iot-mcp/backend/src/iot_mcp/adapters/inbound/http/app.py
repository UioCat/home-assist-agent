"""FastAPI composition root for the standalone IoT MCP backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from iot_mcp.adapters.inbound.http.routes import router
from iot_mcp.application.policy import SafeControlError
from iot_mcp.bootstrap.container import ApplicationContainer, create_container
from iot_mcp.config.settings import Settings
from iot_mcp.ports.device_provider import DeviceProvider


def create_app(
    *,
    settings: Settings | None = None,
    providers: dict[str, DeviceProvider] | None = None,
    container: ApplicationContainer | None = None,
) -> FastAPI:
    if container is not None and (settings is not None or providers is not None):
        raise ValueError("container cannot be combined with settings or providers")
    container = container or create_container(settings, providers=providers)
    settings = container.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await container.startup()
        yield
        await container.shutdown()

    app = FastAPI(title="IoT MCP", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.runtime_container = container
    app.state.providers = container.providers
    app.state.models = container.models
    app.state.devices = container.devices
    app.state.states = container.states
    app.state.operations = container.operations
    app.state.confirmations = container.confirmations
    app.state.webhook_nonces = container.webhook_nonces
    app.state.webhook_channel = container.webhook_channel
    app.state.control = container.control
    app.state.confirmation_service = container.confirmation_service
    app.state.queries = container.queries
    app.state.provider_status = container.provider_status

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

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            404: "not_found",
            405: "method_not_allowed",
        }.get(error.status_code, "http_error")
        message = {
            404: "resource was not found",
            405: "method is not allowed",
        }.get(error.status_code, "HTTP request failed")
        return _error_response(
            request,
            status_code=error.status_code,
            code=code,
            message=message,
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
    _add_spa_fallback(app, settings)
    return app


def _add_spa_fallback(app: FastAPI, settings: Settings) -> None:
    dist = (
        Path(settings.web_dist_path)
        if settings.web_dist_path
        else Path(__file__).resolve().parents[6] / "web" / "dist"
    )
    index = dist / "index.html"
    if not index.is_file():
        return

    @app.get("/{asset_path:path}", include_in_schema=False)
    async def spa_fallback(asset_path: str) -> FileResponse:
        if asset_path in {"api", "mcp"} or asset_path.startswith(("api/", "mcp/")):
            raise HTTPException(status_code=404)
        candidate = (dist / asset_path).resolve()
        if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


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

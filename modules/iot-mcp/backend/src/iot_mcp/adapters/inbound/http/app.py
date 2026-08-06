"""FastAPI composition root for the standalone IoT MCP backend."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from iot_mcp.adapters.inbound.http.routes import router
from iot_mcp.application.policy import SafeControlError
from iot_mcp.audit import AuditUnavailableError
from iot_mcp.bootstrap.container import ApplicationContainer, create_container
from iot_mcp.config.settings import Settings
from iot_mcp.ports.device_provider import DeviceProvider

logger = logging.getLogger(__name__)


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
        if not settings.auth_enabled:
            logger.warning(
                "IoT MCP HTTP authentication is disabled; use local development only"
            )
        await container.startup()
        try:
            yield
        finally:
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
    app.state.audit = container.audit
    app.state.control = container.control
    app.state.confirmation_service = container.confirmation_service
    app.state.queries = container.queries
    app.state.provider_status = container.provider_status

    @app.middleware("http")
    async def audit_message_middleware(request: Request, call_next: Any):
        message_id = str(uuid4())
        request.state.message_id = message_id
        request.state.request_id = message_id
        if not request.url.path.startswith("/api/"):
            response = await call_next(request)
            _set_message_headers(response, message_id)
            return response

        raw_request = await request.body()
        try:
            await container.audit.record(
                message_id=message_id,
                event_type="user.request",
                service="http_api",
                payload={
                    "method": request.method,
                    "path": request.url.path,
                    "query": request.url.query,
                    "body": _decode_body(raw_request),
                },
            )
        except AuditUnavailableError:
            return _audit_unavailable_response(message_id)

        response = await call_next(request)
        raw_response = b"".join(
            [chunk async for chunk in response.body_iterator]
        )
        try:
            await container.audit.record(
                message_id=message_id,
                event_type="user.response",
                service="http_api",
                payload={
                    "status_code": response.status_code,
                    "body": _decode_body(raw_response),
                },
                status="error" if response.status_code >= 400 else "success",
                error_code=(
                    _response_error_code(raw_response)
                    if response.status_code >= 400
                    else None
                ),
            )
        except AuditUnavailableError:
            return _audit_unavailable_response(message_id)
        rebuilt = Response(
            content=raw_response,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
            background=response.background,
        )
        _set_message_headers(rebuilt, message_id)
        return rebuilt

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
    message_id = getattr(request.state, "message_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "message_id": message_id,
                "request_id": message_id,
            }
        },
        headers={"X-Message-ID": message_id, "X-Request-ID": message_id},
    )


def _decode_body(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", errors="replace")


def _response_error_code(body: bytes) -> str | None:
    decoded = _decode_body(body)
    if not isinstance(decoded, dict):
        return None
    error = decoded.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return str(code) if code is not None else None


def _set_message_headers(response: Response, message_id: str) -> None:
    response.headers["X-Message-ID"] = message_id
    response.headers["X-Request-ID"] = message_id


def _audit_unavailable_response(message_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "audit_unavailable",
                "message": "audit persistence is unavailable",
                "retryable": True,
                "message_id": message_id,
                "request_id": message_id,
            }
        },
        headers={"X-Message-ID": message_id, "X-Request-ID": message_id},
    )


app = create_app()

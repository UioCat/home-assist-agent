"""Stable, autonomous-only MCP tool surface."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from iot_mcp.application.policy import ControlAction, SafeControlError, TrustedPrincipal
from iot_mcp.application.safe_dto import operation_public_dto, redact_sensitive
from iot_mcp.bootstrap.container import ApplicationContainer
from iot_mcp.domain.enums import OperationStatus
from iot_mcp.domain.models import utc_now
from mcp.server.fastmcp import FastMCP


def create_mcp_server(
    container: ApplicationContainer,
    *,
    lifespan: Callable[[FastMCP], AbstractAsyncContextManager[Any]] | None = None,
) -> FastMCP:
    """Create exactly the public tools; confirmation decisions stay out of MCP."""
    server = FastMCP(
        "IoT MCP",
        instructions="Use these tools to query and control registered IoT devices.",
        host=container.settings.mcp_host,
        port=container.settings.mcp_port,
        lifespan=lifespan,
    )

    @server.tool(name="list_thing_models")
    async def list_thing_models() -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            return _result(data=[_json(item) for item in await container.queries.list_models()])

        return await _safe_call(operation)

    @server.tool(name="get_thing_model")
    async def get_thing_model(model_id: Any) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            item = await container.models.get_model_version(_required_string(model_id, "model_id"))
            if item is None:
                raise SafeControlError(
                    "thing_model_not_found", "thing model was not found", status_code=404
                )
            return _result(data=_json(item))

        return await _safe_call(operation)

    @server.tool(name="list_devices")
    async def list_devices() -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            return _result(data=[_json(item) for item in await container.queries.list_devices()])

        return await _safe_call(operation)

    @server.tool(name="get_device")
    async def get_device(device_id: Any) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            normalized = _required_string(device_id, "device_id")
            device = await container.queries.get_device(normalized)
            bindings = await container.devices.list_bindings(normalized)
            return _result(data={"device": _json(device), "bindings": _json(bindings)})

        return await _safe_call(operation)

    @server.tool(name="get_device_state")
    async def get_device_state(device_id: Any) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            state = await container.queries.read_state(_required_string(device_id, "device_id"))
            return _result(data=_json(state), observed_at=state.observed_at)

        return await _safe_call(operation)

    @server.tool(name="set_device_properties")
    async def set_device_properties(
        device_id: Any, values: Any, idempotency_key: Any
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            normalized_values = _required_object(values, "values")
            request_id = str(uuid4())
            normalized_key = _required_string(idempotency_key, "idempotency_key")
            control_operation = await container.control.submit(
                device_id=_required_string(device_id, "device_id"),
                action=ControlAction.properties(normalized_values),
                principal=TrustedPrincipal.mcp("mcp"),
                idempotency_key=f"mcp:{normalized_key}",
            )
            return _operation_result(control_operation, request_id=request_id)

        return await _safe_call(operation)

    @server.tool(name="invoke_device_service")
    async def invoke_device_service(
        device_id: Any,
        identifier: Any,
        idempotency_key: Any,
        inputs: Any = None,
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            request_id = str(uuid4())
            normalized_key = _required_string(idempotency_key, "idempotency_key")
            control_operation = await container.control.submit(
                device_id=_required_string(device_id, "device_id"),
                action=ControlAction.invoke_service(
                    _required_string(identifier, "identifier"),
                    {} if inputs is None else _required_object(inputs, "inputs"),
                ),
                principal=TrustedPrincipal.mcp("mcp"),
                idempotency_key=f"mcp:{normalized_key}",
            )
            return _operation_result(control_operation, request_id=request_id)

        return await _safe_call(operation)

    @server.tool(name="get_operation")
    async def get_operation(operation_id: Any) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            item = await container.queries.get_operation(
                _required_string(operation_id, "operation_id")
            )
            return _operation_result(item)

        return await _safe_call(operation)

    @server.tool(name="query_device_events")
    async def query_device_events(device_id: Any, limit: Any = 100) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            normalized_limit = _required_limit(limit)
            normalized_device_id = _required_string(device_id, "device_id")
            await container.queries.get_device(normalized_device_id)
            events = await container.states.list_events(
                normalized_device_id, limit=normalized_limit
            )
            return _result(data=[_json(item) for item in events])

        return await _safe_call(operation)

    return server


async def _safe_call(callback: Any) -> dict[str, Any]:
    try:
        return await callback()
    except SafeControlError as error:
        return _result(
            status="failed",
            error={"code": error.code, "message": error.message, "retryable": error.retryable},
        )
    except (TypeError, ValueError, ValidationError):
        return _result(
            status="failed",
            error={
                "code": "invalid_request",
                "message": "tool input is invalid",
                "retryable": False,
            },
        )
    except Exception:
        return _result(
            status="failed",
            error={
                "code": "internal_error",
                "message": "the tool could not complete the request",
                "retryable": True,
            },
        )


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SafeControlError(
            "invalid_request", f"{name} must be a non-empty string", status_code=422
        )
    return value


def _required_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SafeControlError("invalid_request", f"{name} must be an object", status_code=422)
    return value


def _required_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise SafeControlError(
            "invalid_request", "limit must be an integer between 1 and 100", status_code=422
        )
    return value


def _operation_result(operation: Any, *, request_id: str | None = None) -> dict[str, Any]:
    confirmation_required = operation.status is OperationStatus.PENDING_CONFIRMATION
    data: dict[str, Any] = {"operation": operation_public_dto(operation)}
    if confirmation_required:
        data["confirmation_required"] = True
        confirmation_id = (operation.result or {}).get("confirmation_id")
        if confirmation_id:
            data["confirmation_id"] = confirmation_id
    return _result(
        request_id=request_id,
        operation_id=operation.operation_id,
        status=operation.status.value,
        data=data,
        confirmation_required=confirmation_required,
    )


def _result(
    *,
    data: Any = None,
    request_id: str | None = None,
    operation_id: str | None = None,
    status: str = "succeeded",
    error: dict[str, Any] | None = None,
    observed_at: datetime | None = None,
    confirmation_required: bool = False,
) -> dict[str, Any]:
    result = {
        "request_id": request_id or str(uuid4()),
        "operation_id": operation_id,
        "status": status,
        "data": _json(data if data is not None else {}),
        "error": error,
        "observed_at": (observed_at or utc_now()).isoformat(),
    }
    if confirmation_required:
        result["confirmation_required"] = True
    return result


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): _json(item)
            for key, item in redact_sensitive(value).items()
        }
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value

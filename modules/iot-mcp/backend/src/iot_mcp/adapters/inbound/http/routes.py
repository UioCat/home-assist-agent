"""FastAPI routes for the standalone IoT control module."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError

from iot_mcp.adapters.inbound.http.auth import (
    SessionCodec,
    verified_session_payload,
    verify_admin_token,
)
from iot_mcp.adapters.inbound.http.console_dto import (
    confirmation_console_dto,
    operation_console_dto,
    operation_public_dto,
)
from iot_mcp.adapters.inbound.http.dependencies import (
    authenticated,
    interactive_principal,
    write_principal,
)
from iot_mcp.adapters.inbound.http.schemas import (
    ConfirmationDecisionRequest,
    PropertyWriteRequest,
    ServiceInvokeRequest,
    ThingModelImportRequest,
    WebhookDecisionRequest,
)
from iot_mcp.application.policy import ControlAction, SafeControlError, TrustedPrincipal
from iot_mcp.application.sync_service import DeviceSyncService
from iot_mcp.domain.enums import ConfirmationDecision, ModelStatus, OperationStatus
from iot_mcp.domain.models import ThingModelVersion, ThingProduct
from iot_mcp.domain.tsl import TslDocument

router = APIRouter(prefix="/api/v1")


@router.post("/auth/session")
async def create_session(
    request: Request, response: Response
) -> dict[str, bool | str | None]:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return _disabled_auth_session()
    verify_admin_token(request, settings)
    actor = sorted(settings.allowed_confirmation_actors)[0]
    session, csrf = SessionCodec(
        settings.session_signing_secret, settings.session_ttl_seconds
    ).issue(actor)
    response.set_cookie(
        settings.session_cookie_name,
        session,
        max_age=settings.session_ttl_seconds,
        secure=settings.secure_cookies,
        httponly=settings.cookie_http_only,
        samesite=settings.cookie_same_site,
        path="/api/v1",
    )
    return {
        "auth_enabled": True,
        "csrf_token": csrf,
        "expires_at": (
            datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
        ).isoformat(),
    }


@router.get("/auth/session")
async def get_session(request: Request) -> dict[str, bool | str | None]:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return _disabled_auth_session()
    payload = verified_session_payload(request, settings)
    return {
        "auth_enabled": True,
        "csrf_token": payload["csrf"],
        "expires_at": datetime.fromtimestamp(payload["exp"], UTC).isoformat(),
    }


def _disabled_auth_session() -> dict[str, bool | str | None]:
    return {
        "auth_enabled": False,
        "csrf_token": None,
        "expires_at": None,
    }


@router.get("/thing-models")
async def list_thing_models(
    request: Request, _: TrustedPrincipal = Depends(authenticated)
) -> list[dict[str, Any]]:
    products = await request.app.state.queries.list_models()
    return [product.model_dump(mode="json") for product in products]


@router.post("/thing-models", status_code=201)
async def import_thing_model(
    payload: ThingModelImportRequest,
    request: Request,
    _: TrustedPrincipal = Depends(write_principal),
) -> dict[str, Any]:
    document = TslDocument.model_validate(payload.tsl)
    product_key = document.profile["productKey"]
    canonical = document.model_dump(mode="json", by_alias=True)
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    existing = await request.app.state.models.get_product_by_key(product_key)
    if existing is not None and existing.source != "http":
        raise SafeControlError(
            "system_product_protected",
            "system-generated product identity cannot be overwritten",
            status_code=409,
        )
    product = existing
    if product is None:
        product = await request.app.state.models.upsert_product(
            ThingProduct(
                product_key=product_key,
                name=payload.name,
                source="http",
                capability_fingerprint=fingerprint,
            )
        )
    versions = await request.app.state.models.list_model_versions(product.product_id)
    model = await request.app.state.models.add_model_version(
        ThingModelVersion(
            product_id=product.product_id,
            version=max((item.version for item in versions), default=0) + 1,
            status=ModelStatus.DRAFT,
            tsl_json=canonical,
        )
    )
    return {
        "product": product.model_dump(mode="json"),
        "model": model.model_dump(mode="json"),
    }


@router.get("/thing-models/{model_id}:export")
async def export_thing_model(
    model_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(authenticated),
) -> Response:
    model = await request.app.state.models.get_model_version(model_id)
    if model is None:
        raise SafeControlError(
            "thing_model_not_found", "thing model was not found", status_code=404
        )
    return Response(
        content=json.dumps(
            model.tsl_json,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="thing-model-v{model.version}.json"'
            )
        },
    )


@router.get("/thing-models/{product_id}/versions")
async def list_thing_model_versions(
    product_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(authenticated),
) -> list[dict[str, Any]]:
    models = await request.app.state.models.list_model_versions(product_id)
    return [model.model_dump(mode="json") for model in models]


@router.post("/thing-models/{model_id}:validate")
async def validate_thing_model(
    model_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(write_principal),
) -> dict[str, Any]:
    model = await request.app.state.models.get_model_version(model_id)
    if model is None:
        raise SafeControlError(
            "thing_model_not_found", "thing model was not found", status_code=404
        )
    TslDocument.model_validate(model.tsl_json)
    return {"valid": True, "model_version_id": model.model_version_id}


@router.get("/thing-models/{model_id}")
async def get_thing_model(
    model_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(authenticated),
) -> dict[str, Any]:
    model = await request.app.state.models.get_model_version(model_id)
    if model is None:
        raise SafeControlError(
            "thing_model_not_found", "thing model was not found", status_code=404
        )
    return model.model_dump(mode="json")


@router.post("/thing-models/{model_id}:publish")
async def publish_thing_model(
    model_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(write_principal),
) -> dict[str, Any]:
    model = await request.app.state.models.get_model_version(model_id)
    if model is None:
        raise SafeControlError(
            "thing_model_not_found", "thing model was not found", status_code=404
        )
    TslDocument.model_validate(model.tsl_json)
    try:
        published = await request.app.state.models.publish_model_version(
            model_id
        )
    except ValueError as error:
        raise SafeControlError(
            "model_transition_invalid",
            "only a draft model version can be published",
            status_code=409,
        ) from error
    return published.model_dump(mode="json")


@router.post("/thing-models/{model_id}:archive")
async def archive_thing_model(
    model_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(write_principal),
) -> dict[str, Any]:
    try:
        archived = await request.app.state.models.archive_model_version(
            model_id
        )
    except KeyError as error:
        raise SafeControlError(
            "thing_model_not_found", "thing model was not found", status_code=404
        ) from error
    except ValueError as error:
        raise SafeControlError(
            "model_transition_invalid",
            "only a draft model version can be archived",
            status_code=409,
        ) from error
    return archived.model_dump(mode="json")


@router.get("/devices")
async def list_devices(
    request: Request, _: TrustedPrincipal = Depends(authenticated)
) -> list[dict[str, Any]]:
    devices = await request.app.state.queries.list_devices()
    return [device.model_dump(mode="json") for device in devices]


@router.get("/device-cards")
async def list_device_cards(
    request: Request, _: TrustedPrincipal = Depends(authenticated)
) -> list[dict[str, Any]]:
    cards = await request.app.state.queries.list_device_cards()
    return [dict(card) for card in cards]


@router.get("/devices/{device_id}")
async def get_device(
    device_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(authenticated),
) -> dict[str, Any]:
    device = await request.app.state.queries.get_device(device_id)
    bindings = await request.app.state.devices.list_bindings(device_id)
    feature_bindings = await request.app.state.devices.list_feature_bindings(device_id)
    models = (
        await request.app.state.models.list_model_versions(device.product_id)
        if device.product_id
        else []
    )
    bound_model = (
        await request.app.state.models.get_model_version(
            device.model_version_id
        )
        if device.model_version_id
        else None
    )
    return {
        "device": device.model_dump(mode="json"),
        "bindings": [binding.model_dump(mode="json") for binding in bindings],
        "feature_bindings": [binding.model_dump(mode="json") for binding in feature_bindings],
        "model_versions": [model.model_dump(mode="json") for model in models],
        "bound_model": (
            bound_model.model_dump(mode="json") if bound_model else None
        ),
    }


@router.get("/devices/{device_id}/state")
async def get_device_state(
    device_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(authenticated),
) -> dict[str, Any]:
    state = await request.app.state.queries.read_state(
        device_id, message_id=request.state.message_id
    )
    return state.model_dump(mode="json")


@router.post("/devices/{device_id}/properties:write")
async def write_properties(
    device_id: str,
    payload: PropertyWriteRequest,
    request: Request,
    response: Response,
    principal: TrustedPrincipal = Depends(write_principal),
) -> dict[str, Any]:
    operation = await request.app.state.control.submit(
        device_id=device_id,
        action=ControlAction.properties(payload.values),
        principal=principal,
        idempotency_key=request.headers.get("idempotency-key", ""),
        message_id=request.state.message_id,
    )
    if operation.status is OperationStatus.PENDING_CONFIRMATION:
        response.status_code = 202
    return operation_public_dto(operation)


@router.post("/devices/{device_id}/services/{identifier}:invoke")
async def invoke_service(
    device_id: str,
    identifier: str,
    payload: ServiceInvokeRequest,
    request: Request,
    response: Response,
    principal: TrustedPrincipal = Depends(write_principal),
) -> dict[str, Any]:
    operation = await request.app.state.control.submit(
        device_id=device_id,
        action=ControlAction.invoke_service(identifier, payload.inputs),
        principal=principal,
        idempotency_key=request.headers.get("idempotency-key", ""),
        message_id=request.state.message_id,
    )
    if operation.status is OperationStatus.PENDING_CONFIRMATION:
        response.status_code = 202
    return operation_public_dto(operation)


@router.get("/operations")
async def list_operations(
    request: Request, _: TrustedPrincipal = Depends(authenticated)
) -> list[dict[str, Any]]:
    operations = await request.app.state.operations.list_operations()
    result: list[dict[str, Any]] = []
    for operation in operations:
        device = await request.app.state.devices.get_device(operation.device_id)
        result.append(operation_console_dto(operation, device))
    return result


@router.get("/operations/{operation_id}")
async def get_operation(
    operation_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(authenticated),
) -> dict[str, Any]:
    operation = await request.app.state.queries.get_operation(operation_id)
    return operation_public_dto(operation)


@router.get("/confirmations")
async def list_confirmations(
    request: Request,
    decision: ConfirmationDecision | None = None,
    _: TrustedPrincipal = Depends(authenticated),
) -> list[dict[str, Any]]:
    confirmations = await request.app.state.confirmations.list_requests(decision=decision)
    result: list[dict[str, Any]] = []
    for confirmation in confirmations:
        operation = await request.app.state.operations.get_operation(confirmation.operation_id)
        device = (
            await request.app.state.devices.get_device(operation.device_id)
            if operation
            else None
        )
        result.append(confirmation_console_dto(confirmation, operation, device))
    return result


@router.post("/confirmations/{confirmation_id}:approve")
async def approve_confirmation(
    confirmation_id: str,
    payload: ConfirmationDecisionRequest,
    request: Request,
    principal: TrustedPrincipal = Depends(interactive_principal),
) -> dict[str, Any]:
    operation = await request.app.state.confirmation_service.decide(
        confirmation_id=confirmation_id,
        decision="approve",
        actor=principal.actor_id,
        action_hash=payload.action_hash,
        message_id=request.state.message_id,
    )
    return operation_public_dto(operation)


@router.post("/confirmations/{confirmation_id}:reject")
async def reject_confirmation(
    confirmation_id: str,
    payload: ConfirmationDecisionRequest,
    request: Request,
    principal: TrustedPrincipal = Depends(interactive_principal),
) -> dict[str, Any]:
    operation = await request.app.state.confirmation_service.decide(
        confirmation_id=confirmation_id,
        decision="reject",
        actor=principal.actor_id,
        action_hash=payload.action_hash,
        message_id=request.state.message_id,
    )
    return operation_public_dto(operation)


@router.get("/device-events")
async def list_device_events(
    request: Request,
    device_id: str | None = None,
    _: TrustedPrincipal = Depends(authenticated),
) -> list[dict[str, Any]]:
    events = await request.app.state.states.list_events(device_id)
    return [event.model_dump(mode="json") for event in events]


@router.get("/providers")
async def list_providers(
    request: Request, _: TrustedPrincipal = Depends(authenticated)
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for provider_id, provider in sorted(request.app.state.providers.items()):
        try:
            health = await provider.health(message_id=request.state.message_id)
        except Exception:
            status = request.app.state.provider_status.get(provider_id, "unavailable")
            detail = "provider health check failed"
        else:
            status = health.status
            detail = health.detail
        result.append(
            {
                "provider_id": provider_id,
                "provider_type": provider.provider_type,
                "status": status,
                "detail": detail,
            }
        )
    return result


@router.get("/message-channels")
async def list_message_channels(
    request: Request, _: TrustedPrincipal = Depends(authenticated)
) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    return [
        {
            "channel_id": "signed-webhook",
            "status": "configured" if settings.webhook_send_url else "not_configured",
            "callback_path": "/api/v1/message-channels/signed-webhook/callbacks",
            "allowed_actor_count": len(settings.allowed_confirmation_actors),
        }
    ]


@router.post("/message-channels/{channel}/callbacks")
async def webhook_callback(
    channel: str,
    request: Request,
) -> dict[str, Any]:
    if channel != "signed-webhook":
        raise SafeControlError(
            "message_channel_not_found", "message channel was not found", status_code=404
        )
    raw_body = await request.body()
    await request.app.state.webhook_channel.verify(raw_body, request.headers)
    try:
        payload = WebhookDecisionRequest.model_validate_json(raw_body)
    except ValidationError as error:
        raise SafeControlError(
            "invalid_request", "request body is invalid", status_code=422
        ) from error
    request.app.state.webhook_channel.verify_actor(payload.actor)
    operation = await request.app.state.confirmation_service.decide(
        confirmation_id=payload.confirmation_id,
        decision=payload.decision,
        actor=payload.actor,
        action_hash=payload.action_hash,
        message_id=request.state.message_id,
    )
    return operation_public_dto(operation)


@router.post("/providers/{provider_id}:sync")
async def sync_provider(
    provider_id: str,
    request: Request,
    _: TrustedPrincipal = Depends(write_principal),
) -> dict[str, int]:
    provider = request.app.state.providers.get(provider_id)
    if provider is None:
        raise SafeControlError(
            "provider_not_found", "provider was not found", status_code=404
        )
    result = await DeviceSyncService(
        provider,
        request.app.state.models,
        request.app.state.devices,
        request.app.state.states,
        request.app.state.audit,
    ).sync(message_id=request.state.message_id, trigger="manual")
    return {
        "discovered": result.discovered,
        "upserted": result.upserted,
        "missing": result.missing,
        "snapshots": result.snapshots,
    }

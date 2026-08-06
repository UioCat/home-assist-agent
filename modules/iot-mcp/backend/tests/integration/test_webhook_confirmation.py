from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from iot_mcp.adapters.inbound.http.app import create_app
from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.adapters.outbound.persistence.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from iot_mcp.adapters.outbound.persistence.repositories import WebhookNonceRepository
from iot_mcp.adapters.outbound.webhook.channel import SignedWebhookMessageChannel
from iot_mcp.application.policy import SafeControlError
from iot_mcp.config.settings import Settings
from iot_mcp.domain.enums import RiskLevel
from iot_mcp.domain.models import DeviceInstance, ProviderDeviceBinding


def _signed_headers(secret: str, body: bytes, *, nonce: str, timestamp: int | None = None):
    timestamp = timestamp or int(time.time())
    signed = f"{timestamp}.{nonce}.".encode() + body
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-IoT-Timestamp": str(timestamp),
        "X-IoT-Nonce": nonce,
        "X-IoT-Signature": f"sha256={signature}",
    }


@pytest.mark.asyncio
async def test_signed_webhook_approves_bound_action_once_and_rejects_replay(tmp_path) -> None:
    secret = "webhook-secret"
    settings = Settings(
        auth_enabled=True,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'webhook.db'}",
        admin_token="admin",
        session_signing_secret="session-secret",
        webhook_secret=secret,
        allowed_confirmation_actors={"owner"},
    )
    provider = MockDeviceProvider()
    app = create_app(settings=settings, providers={"mock": provider})
    async with app.router.lifespan_context(app):
        await app.state.devices.upsert_device(
            DeviceInstance(
                device_id="door",
                provider_id="mock",
                display_name="Door",
                risk_level=RiskLevel.HIGH,
            )
        )
        await app.state.devices.upsert_binding(
            ProviderDeviceBinding(
                device_id="door",
                provider_type="mock",
                external_device_ref="mock:lock:front_door",
                binding_revision=3,
            )
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://test") as client:
            pending_response = await client.post(
                "/api/v1/devices/door/properties:write",
                headers={"Authorization": "Bearer admin", "Idempotency-Key": "hook-op"},
                json={"values": {"LockState": "UNLOCK"}},
            )
            operation_id = pending_response.json()["operation_id"]
            confirmation = await app.state.confirmations.get_by_operation(operation_id)
            body = json.dumps(
                {
                    "actor": "owner",
                    "decision": "approve",
                    "confirmation_id": confirmation.confirmation_id,
                    "action_hash": confirmation.action_hash,
                },
                separators=(",", ":"),
            ).encode()
            headers = _signed_headers(secret, body, nonce="unique-nonce")

            approved = await client.post(
                "/api/v1/message-channels/signed-webhook/callbacks",
                content=body,
                headers=headers,
            )
            replay = await client.post(
                "/api/v1/message-channels/signed-webhook/callbacks",
                content=body,
                headers=headers,
            )

        assert approved.status_code == 200
        assert approved.json()["status"] == "succeeded"
        assert replay.status_code == 409
        assert replay.json()["error"]["code"] == "webhook_replay"


@pytest.mark.asyncio
async def test_webhook_rejects_stale_timestamp_bad_actor_and_action_substitution(tmp_path) -> None:
    secret = "webhook-secret"
    settings = Settings(
        auth_enabled=True,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'bad-webhook.db'}",
        admin_token="admin",
        session_signing_secret="session-secret",
        webhook_secret=secret,
        allowed_confirmation_actors={"owner"},
        webhook_timestamp_tolerance_seconds=30,
    )
    app = create_app(settings=settings, providers={"mock": MockDeviceProvider()})
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://test") as client:
            body = json.dumps(
                {
                    "actor": "intruder",
                    "decision": "approve",
                    "confirmation_id": "missing",
                    "action_hash": "forged",
                    "action": {"kind": "properties", "values": {"LockState": "UNLOCK"}},
                },
                separators=(",", ":"),
            ).encode()
            stale = await client.post(
                "/api/v1/message-channels/signed-webhook/callbacks",
                content=body,
                headers=_signed_headers(
                    secret, body, nonce="stale", timestamp=int(time.time()) - 60
                ),
            )
            invalid_body = await client.post(
                "/api/v1/message-channels/signed-webhook/callbacks",
                content=body,
                headers=_signed_headers(secret, body, nonce="substitution"),
            )
            actor_body = json.dumps(
                {
                    "actor": "intruder",
                    "decision": "approve",
                    "confirmation_id": "missing",
                    "action_hash": "forged",
                },
                separators=(",", ":"),
            ).encode()
            bad_actor = await client.post(
                "/api/v1/message-channels/signed-webhook/callbacks",
                content=actor_body,
                headers=_signed_headers(secret, actor_body, nonce="bad-actor"),
            )

        assert stale.status_code == 401
        assert stale.json()["error"]["code"] == "webhook_timestamp_invalid"
        assert invalid_body.status_code == 422
        assert invalid_body.json()["error"]["code"] == "invalid_request"
        assert bad_actor.status_code == 403
        assert bad_actor.json()["error"]["code"] == "actor_not_authorized"


@pytest.mark.asyncio
async def test_nonce_consumption_is_durable_and_atomic_across_channel_instances(
    tmp_path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'nonces.db'}"
    secret = "durable-webhook-secret"
    body = b'{"actor":"owner"}'
    headers = {
        key.lower(): value
        for key, value in _signed_headers(secret, body, nonce="durable-nonce").items()
    }
    engine = create_database_engine(database_url)
    await initialize_database(engine)
    sessions = create_session_factory(engine)
    channels = [
        SignedWebhookMessageChannel(
            secret=secret,
            allowed_actor_ids={"owner"},
            nonces=WebhookNonceRepository(sessions),
        )
        for _ in range(20)
    ]

    results = await asyncio.gather(
        *(channel.verify(body, headers) for channel in channels),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 1
    assert sum(
        isinstance(result, SafeControlError) and result.code == "webhook_replay"
        for result in results
    ) == 19
    await engine.dispose()

    reopened = create_database_engine(database_url)
    await initialize_database(reopened)
    channel = SignedWebhookMessageChannel(
        secret=secret,
        allowed_actor_ids={"owner"},
        nonces=WebhookNonceRepository(create_session_factory(reopened)),
    )
    with pytest.raises(SafeControlError, match="already used"):
        await channel.verify(body, headers)
    await reopened.dispose()

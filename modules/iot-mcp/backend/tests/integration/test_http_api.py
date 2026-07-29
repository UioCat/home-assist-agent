from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from iot_mcp.adapters.inbound.http.app import create_app
from iot_mcp.adapters.inbound.http.auth import SessionCodec
from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.config.settings import Settings
from iot_mcp.domain.enums import OperationStatus, RiskLevel
from iot_mcp.domain.models import DeviceInstance, ProviderDeviceBinding


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        admin_token="admin-secret",
        machine_tokens={"machine-secret": "agent"},
        session_signing_secret="session-secret-with-enough-entropy",
        webhook_secret="webhook-secret-with-enough-entropy",
        allowed_confirmation_actors={"owner"},
        secure_cookies=True,
    )


async def _client(settings):
    app = create_app(settings=settings, providers={"mock": MockDeviceProvider()})
    async with app.router.lifespan_context(app):
        await app.state.devices.upsert_device(
            DeviceInstance(
                device_id="door",
                provider_id="mock",
                display_name="Front door",
                risk_level=RiskLevel.HIGH,
            )
        )
        await app.state.devices.upsert_binding(
            ProviderDeviceBinding(
                device_id="door",
                provider_type="mock",
                external_device_ref="mock:lock:front_door",
                binding_revision=1,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            yield app, client


@pytest.mark.asyncio
async def test_admin_token_cannot_promote_body_to_human_interactive(settings) -> None:
    async for app, client in _client(settings):
        response = await client.post(
            "/api/v1/devices/door/properties:write",
            headers={"Authorization": "Bearer admin-secret", "Idempotency-Key": "api-1"},
            json={
                "values": {"LockState": "UNLOCK"},
                "interaction_mode": "human_interactive",
                "initiator": "owner",
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert len(await app.state.operations.list_operations()) == 0


@pytest.mark.asyncio
async def test_direct_admin_token_high_risk_is_autonomous(settings) -> None:
    async for app, client in _client(settings):
        response = await client.post(
            "/api/v1/devices/door/properties:write",
            headers={"Authorization": "Bearer admin-secret", "Idempotency-Key": "api-2"},
            json={"values": {"LockState": "UNLOCK"}},
        )

        assert response.status_code == 202
        assert response.json()["status"] == "pending_confirmation"
        assert (await app.state.confirmations.get_by_operation(response.json()["operation_id"]))


@pytest.mark.asyncio
async def test_admin_token_cannot_approve_its_own_pending_operation(settings) -> None:
    async for app, client in _client(settings):
        pending_response = await client.post(
            "/api/v1/devices/door/properties:write",
            headers={
                "Authorization": "Bearer admin-secret",
                "Idempotency-Key": "api-approve",
            },
            json={"values": {"LockState": "UNLOCK"}},
        )
        confirmation = await app.state.confirmations.get_by_operation(
            pending_response.json()["operation_id"]
        )
        response = await client.post(
            f"/api/v1/confirmations/{confirmation.confirmation_id}:approve",
            headers={"Authorization": "Bearer admin-secret"},
            json={"action_hash": confirmation.action_hash},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "interactive_auth_required"


@pytest.mark.asyncio
async def test_signed_session_and_bound_csrf_execute_human_high_risk(settings) -> None:
    async for app, client in _client(settings):
        login = await client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer admin-secret"},
        )
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "Secure" in login.headers["set-cookie"]
        assert "samesite=strict" in login.headers["set-cookie"].lower()
        csrf = login.json()["csrf_token"]

        missing_csrf = await client.post(
            "/api/v1/devices/door/properties:write",
            headers={"Idempotency-Key": "api-3"},
            json={"values": {"LockState": "UNLOCK"}},
        )
        response = await client.post(
            "/api/v1/devices/door/properties:write",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": "api-4"},
            json={"values": {"LockState": "UNLOCK"}},
        )

        assert missing_csrf.status_code == 403
        assert response.status_code == 200
        assert response.json()["status"] == "succeeded"
        confirmation = await app.state.confirmations.get_by_operation(
            response.json()["operation_id"]
        )
        assert confirmation is None


@pytest.mark.asyncio
async def test_signed_session_bootstrap_restores_bound_csrf_without_renewal(
    settings,
) -> None:
    async for _, client in _client(settings):
        login = await client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer admin-secret"},
        )
        restored = await client.get("/api/v1/auth/session")

        assert restored.status_code == 200
        assert restored.json()["csrf_token"] == login.json()["csrf_token"]
        assert restored.json()["expires_at"].endswith("+00:00")
        assert "set-cookie" not in restored.headers

        write = await client.post(
            "/api/v1/devices/door/properties:write",
            headers={
                "X-CSRF-Token": restored.json()["csrf_token"],
                "Idempotency-Key": "restored-session",
            },
            json={"values": {"LockState": "LOCK"}},
        )
        assert write.status_code == 200


@pytest.mark.asyncio
async def test_expired_session_bootstrap_returns_stable_non_leaking_error(settings) -> None:
    async for _, client in _client(settings):
        expired, _ = SessionCodec(settings.session_signing_secret, -1).issue("owner")
        client.cookies.set(settings.session_cookie_name, expired, path="/api/v1")

        response = await client.get("/api/v1/auth/session")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "session_invalid"
        assert set(response.json()["error"]) == {
            "code",
            "message",
            "retryable",
            "request_id",
        }
        assert expired not in response.text


@pytest.mark.asyncio
async def test_web_session_can_approve_only_the_original_bound_action(settings) -> None:
    async for app, client in _client(settings):
        pending_response = await client.post(
            "/api/v1/devices/door/properties:write",
            headers={
                "Authorization": "Bearer admin-secret",
                "Idempotency-Key": "web-approval",
            },
            json={"values": {"LockState": "UNLOCK"}},
        )
        login = await client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer admin-secret"},
        )
        csrf = login.json()["csrf_token"]
        operation_id = pending_response.json()["operation_id"]
        confirmation_id = pending_response.json()["result"]["confirmation_id"]
        operation = await client.get(
            f"/api/v1/operations/{operation_id}",
            headers={"X-CSRF-Token": csrf},
        )
        confirmation = await app.state.confirmations.get_request(confirmation_id)
        assert confirmation is not None
        approved = await client.post(
            f"/api/v1/confirmations/{confirmation_id}:approve",
            headers={"X-CSRF-Token": csrf},
            json={"action_hash": confirmation.action_hash},
        )

        assert operation.status_code == 200
        assert approved.status_code == 200
        assert approved.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_web_console_read_contract_exposes_operational_collections(settings) -> None:
    async for app, client in _client(settings):
        login = await client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer admin-secret"},
        )
        csrf = login.json()["csrf_token"]
        pending = await client.post(
            "/api/v1/devices/door/properties:write",
            headers={
                "Authorization": "Bearer machine-secret",
                "Idempotency-Key": "console-pending",
            },
            json={
                "values": {
                    "LockState": "UNLOCK",
                    "pin": "839201",
                    "nested": {"authorization": "Bearer provider-secret"},
                }
            },
        )
        confirmation_id = pending.json()["result"]["confirmation_id"]
        await app.state.operations.update_operation(
            pending.json()["operation_id"],
            status=OperationStatus.PENDING_CONFIRMATION,
            provider_request={"token": "provider-request-secret"},
            provider_result={"nested": {"password": "provider-result-secret"}},
        )

        operations = await client.get("/api/v1/operations")
        confirmations = await client.get("/api/v1/confirmations?decision=pending")
        events = await client.get("/api/v1/device-events?device_id=door")
        providers = await client.get("/api/v1/providers")
        channels = await client.get("/api/v1/message-channels")
        device = await client.get("/api/v1/devices/door")

        assert operations.status_code == 200
        assert operations.json()[0]["operation_id"] == pending.json()["operation_id"]
        assert set(operations.json()[0]) == {
            "operation_id",
            "device_id",
            "source_category",
            "source_label",
            "action_kind",
            "action_summary",
            "target",
            "provider_id",
            "provider_type",
            "binding_revision",
            "risk_level",
            "status",
            "created_at",
            "updated_at",
        }
        assert operations.json()[0]["source_label"] == "Machine automation"
        assert confirmations.status_code == 200
        assert confirmations.json()[0]["confirmation"]["confirmation_id"] == confirmation_id
        assert "initiator" not in confirmations.json()[0]["operation"]
        assert "authorized_actor" not in confirmations.json()[0]["confirmation"]
        serialized = operations.text + confirmations.text
        for secret in (
            "machine_token:agent",
            "console-pending",
            "839201",
            "Bearer provider-secret",
            "provider-request-secret",
            "provider-result-secret",
        ):
            assert secret not in serialized
        assert '"pin"' not in serialized.lower()
        assert events.status_code == 200
        assert events.json() == []
        assert providers.status_code == 200
        assert providers.json() == [
            {
                "provider_id": "mock",
                "provider_type": "mock",
                "status": "healthy",
                "detail": None,
            }
        ]
        assert channels.status_code == 200
        assert channels.json() == [
            {
                "channel_id": "signed-webhook",
                "status": "not_configured",
                "callback_path": "/api/v1/message-channels/signed-webhook/callbacks",
                "allowed_actor_count": 1,
            }
        ]
        assert device.status_code == 200
        assert set(device.json()) == {
            "device",
            "bindings",
            "feature_bindings",
            "model_versions",
        }
        assert csrf


@pytest.mark.asyncio
async def test_error_shape_is_stable_and_does_not_echo_secret(settings) -> None:
    async for _, client in _client(settings):
        response = await client.get(
            "/api/v1/devices", headers={"Authorization": "Bearer wrong-secret"}
        )
        body = response.json()

        assert response.status_code == 401
        assert set(body["error"]) == {"code", "message", "retryable", "request_id"}
        assert "wrong-secret" not in response.text


@pytest.mark.asyncio
async def test_router_404_and_405_use_stable_error_contract(settings) -> None:
    async for _, client in _client(settings):
        missing = await client.get("/api/v1/not-a-route")
        wrong_method = await client.delete("/api/v1/devices")

        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        assert set(missing.json()["error"]) == {
            "code",
            "message",
            "retryable",
            "request_id",
        }
        assert wrong_method.status_code == 405
        assert wrong_method.json()["error"]["code"] == "method_not_allowed"

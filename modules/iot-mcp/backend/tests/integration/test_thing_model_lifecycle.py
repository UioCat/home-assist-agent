from __future__ import annotations

from copy import deepcopy

from httpx import ASGITransport, AsyncClient

from iot_mcp.adapters.inbound.http.app import create_app
from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.config.settings import Settings
from iot_mcp.domain.enums import ModelStatus
from iot_mcp.domain.models import DeviceInstance


def _tsl(product_key: str, *, maximum: int = 100) -> dict[str, object]:
    return {
        "schema": "https://iotx-tsl.example/schema.json",
        "profile": {"productKey": product_key},
        "properties": [
            {
                "identifier": "Level",
                "name": "Level",
                "accessMode": "rw",
                "required": False,
                "dataType": {
                    "type": "int",
                    "specs": {"min": 0, "max": maximum},
                },
            }
        ],
        "services": [],
        "events": [],
    }


async def test_manual_model_uses_draft_publish_archive_and_export_lifecycle(
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'models.db'}",
        admin_token="admin-secret",
        session_signing_secret="session-secret-with-enough-entropy",
        webhook_secret="webhook-secret-with-enough-entropy",
        secure_cookies=False,
    )
    app = create_app(settings=settings, providers={"mock": MockDeviceProvider()})
    headers = {"Authorization": "Bearer admin-secret"}

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            first = await client.post(
                "/api/v1/thing-models",
                headers=headers,
                json={
                    "name": "Manual dimmer",
                    "source": "http",
                    "tsl": _tsl("manual-dimmer"),
                },
            )
            assert first.status_code == 201
            first_model = first.json()["model"]
            product = first.json()["product"]
            assert first_model["status"] == "draft"

            validated = await client.post(
                f"/api/v1/thing-models/{first_model['model_version_id']}:validate",
                headers=headers,
            )
            exported = await client.get(
                f"/api/v1/thing-models/{first_model['model_version_id']}:export",
                headers=headers,
            )
            published = await client.post(
                f"/api/v1/thing-models/{first_model['model_version_id']}:publish",
                headers=headers,
            )

            assert validated.json() == {
                "valid": True,
                "model_version_id": first_model["model_version_id"],
            }
            assert exported.status_code == 200
            assert exported.json() == _tsl("manual-dimmer")
            assert "attachment" in exported.headers["content-disposition"]
            assert published.json()["status"] == "active"

            await app.state.devices.upsert_device(
                DeviceInstance(
                    device_id="manual-device",
                    product_id=product["product_id"],
                    model_version_id=first_model["model_version_id"],
                    provider_id="mock",
                    display_name="Manual device",
                )
            )
            second_tsl = _tsl("manual-dimmer", maximum=10)
            second = await client.post(
                "/api/v1/thing-models",
                headers=headers,
                json={"name": "Ignored rename", "tsl": second_tsl},
            )
            second_model = second.json()["model"]
            assert second_model["status"] == "draft"
            assert second.json()["product"]["name"] == "Manual dimmer"

            second_published = await client.post(
                f"/api/v1/thing-models/{second_model['model_version_id']}:publish",
                headers=headers,
            )
            versions = await client.get(
                f"/api/v1/thing-models/{product['product_id']}/versions",
                headers=headers,
            )
            rebound = await app.state.devices.get_device("manual-device")

            assert second_published.json()["status"] == "active"
            assert [item["status"] for item in versions.json()] == [
                "active",
                "archived",
            ]
            assert rebound is not None
            assert rebound.model_version_id == second_model["model_version_id"]

            third = await client.post(
                "/api/v1/thing-models",
                headers=headers,
                json={"name": "Manual dimmer", "tsl": _tsl("manual-dimmer", maximum=5)},
            )
            archived = await client.post(
                f"/api/v1/thing-models/{third.json()['model']['model_version_id']}:archive",
                headers=headers,
            )
            republish_archived = await client.post(
                f"/api/v1/thing-models/{third.json()['model']['model_version_id']}:publish",
                headers=headers,
            )

            assert archived.json()["status"] == "archived"
            assert republish_archived.status_code == 409
            assert republish_archived.json()["error"]["code"] == "model_transition_invalid"


async def test_manual_import_cannot_overwrite_system_product_identity(
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'protected.db'}",
        admin_token="admin-secret",
        session_signing_secret="session-secret-with-enough-entropy",
        webhook_secret="webhook-secret-with-enough-entropy",
        secure_cookies=False,
    )
    app = create_app(settings=settings, providers={"mock": MockDeviceProvider()})
    headers = {"Authorization": "Bearer admin-secret"}

    async with app.router.lifespan_context(app):
        product = await app.state.models.get_product_by_key("mock-light")
        assert product is not None
        before = deepcopy(product.model_dump(mode="json"))
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/v1/thing-models",
                headers=headers,
                json={
                    "name": "Take over generated product",
                    "source": "http",
                    "tsl": _tsl("mock-light"),
                },
            )

        after = await app.state.models.get_product_by_key("mock-light")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "system_product_protected"
        assert after is not None
        assert after.model_dump(mode="json") == before


async def test_device_detail_exposes_the_exact_bound_model(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'device-model.db'}",
        admin_token="admin-secret",
        session_signing_secret="session-secret-with-enough-entropy",
        webhook_secret="webhook-secret-with-enough-entropy",
        secure_cookies=False,
    )
    app = create_app(settings=settings, providers={"mock": MockDeviceProvider()})

    async with app.router.lifespan_context(app):
        device = next(
            item
            for item in await app.state.devices.list_devices()
            if item.display_name == "Desk light"
        )
        assert device.model_version_id is not None
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                f"/api/v1/devices/{device.device_id}",
                headers={"Authorization": "Bearer admin-secret"},
            )

        assert response.status_code == 200
        assert response.json()["device"]["model_version_id"] == device.model_version_id
        assert response.json()["bound_model"]["model_version_id"] == device.model_version_id
        assert (
            response.json()["bound_model"]["status"]
            == ModelStatus.ACTIVE.value
        )

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from iot_mcp.__main__ import main, parse_args
from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.bootstrap.runtime import build_runtime
from iot_mcp.config.settings import Settings
from iot_mcp.ports.device_provider import ProviderEvent


class FailingProvider(MockDeviceProvider):
    provider_id = "failing"

    async def discover(self, *, message_id: str | None = None):
        raise RuntimeError("provider secret must not leak")


class _BlockingSubscription:
    def __init__(self) -> None:
        self.closed = asyncio.Event()

    async def wait(self) -> None:
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()


class LifecycleProvider(MockDeviceProvider):
    provider_id = "lifecycle"

    def __init__(self) -> None:
        super().__init__()
        self.discover_calls = 0
        self.subscribe_calls = 0
        self.sink = None
        self.subscriptions: list[_BlockingSubscription] = []

    async def discover(self, *, message_id: str | None = None):
        self.discover_calls += 1
        inventory = await super().discover(message_id=message_id)
        return inventory.model_copy(update={"provider_id": self.provider_id})

    async def subscribe(self, sink, *, message_id: str | None = None):
        self.subscribe_calls += 1
        if self.subscribe_calls == 1:
            raise RuntimeError("injected disconnect")
        self.sink = sink
        subscription = _BlockingSubscription()
        self.subscriptions.append(subscription)
        return subscription


async def _eventually(predicate, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


async def test_runtime_initializes_and_syncs_mock_provider(tmp_path) -> None:
    runtime = build_runtime(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        providers={"mock": MockDeviceProvider()},
    )
    await runtime.startup()
    try:
        assert runtime.container.provider_status == {"mock": "healthy"}
        devices = await runtime.container.devices.list_devices()
        assert len(devices) == 3
        assert all(device.product_id and device.model_version_id for device in devices)
    finally:
        await runtime.shutdown()


async def test_mcp_lifespan_initializes_and_releases_runtime(tmp_path) -> None:
    runtime = build_runtime(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        providers={"mock": MockDeviceProvider()},
    )

    async with runtime.mcp_lifespan(runtime.mcp_server):
        assert runtime.container.provider_status == {"mock": "healthy"}

    assert runtime.container._started is False


async def test_http_lifespan_releases_runtime_when_body_raises(tmp_path) -> None:
    runtime = build_runtime(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        providers={"mock": MockDeviceProvider()},
    )
    try:
        with pytest.raises(RuntimeError, match="lifespan body failed"):
            async with runtime.http_app.router.lifespan_context(runtime.http_app):
                raise RuntimeError("lifespan body failed")

        assert runtime.container._started is False
        assert runtime.container._background_tasks == {}
    finally:
        await runtime.shutdown()


async def test_runtime_degrades_without_creating_fake_online_devices(tmp_path) -> None:
    runtime = build_runtime(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        providers={"failing": FailingProvider()},
    )
    await runtime.startup()
    try:
        assert runtime.container.provider_status == {"failing": "degraded"}
        assert await runtime.container.devices.list_devices() == []
    finally:
        await runtime.shutdown()


async def test_runtime_owns_reconnecting_subscription_reconciliation_and_event_persistence(
    tmp_path,
) -> None:
    provider = LifecycleProvider()
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}",
            provider_reconnect_delay_seconds=0.01,
            reconcile_interval_seconds=0.02,
        ),
        providers={provider.provider_id: provider},
    )
    await runtime.startup()
    await _eventually(lambda: provider.subscribe_calls >= 2)
    await _eventually(lambda: provider.discover_calls >= 2)
    assert provider.sink is not None
    provider._states["mock:light:desk"].update(
        {"PowerSwitch": True, "Brightness": 73}
    )
    result = provider.sink(
        ProviderEvent(
            device_ref="mock:light:desk",
            identifier="state_changed",
            values={"PowerSwitch": True, "Brightness": 73},
        )
    )
    if result is not None:
        await result
    light = next(
        device
        for device in await runtime.container.devices.list_devices()
        if device.display_name == "Desk light"
    )
    snapshots = await runtime.container.states.latest_snapshots(light.device_id)
    events = await runtime.container.states.list_events(light.device_id)

    assert {item.identifier: item.value for item in snapshots}["Brightness"] == 73
    assert events[0].identifier == "state_changed"
    assert events[0].output_data == {
        "PowerSwitch": True,
        "Brightness": 73,
    }
    assert runtime.container.provider_status[provider.provider_id] == "healthy"

    await runtime.shutdown()

    assert runtime.container._background_tasks == {}
    assert all(subscription.closed.is_set() for subscription in provider.subscriptions)


async def test_spa_fallback_serves_only_non_api_paths(tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>iot</html>")
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
            web_dist_path=str(dist),
        ),
        providers={"mock": MockDeviceProvider()},
    )
    async with runtime.http_app.router.lifespan_context(runtime.http_app):
        async with AsyncClient(
            transport=ASGITransport(runtime.http_app), base_url="http://testserver"
        ) as client:
            spa = await client.get("/devices/desk")
            api = await client.get("/api/not-a-route")
            mcp = await client.get("/mcp")

    assert spa.status_code == 200
    assert spa.text == "<html>iot</html>"
    assert api.status_code == 404
    assert mcp.status_code == 404


async def test_missing_spa_dist_does_not_fabricate_a_page(tmp_path) -> None:
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
            web_dist_path=str(tmp_path / "missing-dist"),
        ),
        providers={"mock": MockDeviceProvider()},
    )
    async with runtime.http_app.router.lifespan_context(runtime.http_app):
        async with AsyncClient(
            transport=ASGITransport(runtime.http_app), base_url="http://testserver"
        ) as client:
            response = await client.get("/")

    assert response.status_code == 404


def test_cli_selects_http_without_starting_a_real_server(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "iot_mcp.__main__.uvicorn.run", lambda app, **kwargs: captured.update(kwargs)
    )

    main(["--mode", "http"])

    assert captured == {"host": "127.0.0.1", "port": 8090}
    assert parse_args(["--mode", "mcp", "--mcp-transport", "stdio"]).mode == "mcp"

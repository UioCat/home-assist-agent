from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from iot_mcp.__main__ import main, parse_args
from iot_mcp.adapters.outbound.mock.provider import MockDeviceProvider
from iot_mcp.bootstrap.runtime import build_runtime
from iot_mcp.config.settings import Settings


class FailingProvider(MockDeviceProvider):
    provider_id = "failing"

    async def discover(self):
        raise RuntimeError("provider secret must not leak")


async def test_runtime_initializes_and_syncs_mock_provider(tmp_path) -> None:
    runtime = build_runtime(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        providers={"mock": MockDeviceProvider()},
    )
    await runtime.startup()
    try:
        assert runtime.container.provider_status == {"mock": "healthy"}
        assert len(await runtime.container.devices.list_devices()) == 3
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

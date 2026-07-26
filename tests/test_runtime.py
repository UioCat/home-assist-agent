from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from home_assist_agent.api.health import CodexHealthProbe
from home_assist_agent.bootstrap import build_app
from home_assist_agent.codex.gateway import ProcessResult
from home_assist_agent.settings import AppSettings


@dataclass
class FakeRunner:
    result: ProcessResult
    calls: list[list[str]]

    async def run(
        self,
        args: list[str],
        stdin: str,
        timeout_seconds: float,
    ) -> ProcessResult:
        self.calls.append(args)
        return self.result


@pytest.mark.asyncio
async def test_codex_probe_reports_installed_and_authenticated() -> None:
    runner = FakeRunner(
        result=ProcessResult(
            returncode=0,
            stdout="Logged in using ChatGPT",
            stderr="",
        ),
        calls=[],
    )
    probe = CodexHealthProbe(
        binary="codex",
        runner=runner,
        binary_resolver=lambda _: "/usr/local/bin/codex",
    )

    installed, authenticated, error_code = await probe.check()

    assert installed is True
    assert authenticated is True
    assert error_code is None
    assert runner.calls == [["/usr/local/bin/codex", "login", "status"]]


@pytest.mark.asyncio
async def test_codex_probe_distinguishes_missing_binary_from_missing_login() -> None:
    missing = CodexHealthProbe(
        binary="codex",
        runner=FakeRunner(ProcessResult(0, "", ""), []),
        binary_resolver=lambda _: None,
    )
    logged_out = CodexHealthProbe(
        binary="codex",
        runner=FakeRunner(
            ProcessResult(1, "", "Not logged in"),
            [],
        ),
        binary_resolver=lambda _: "/usr/local/bin/codex",
    )

    assert await missing.check() == (False, False, "codex_not_found")
    assert await logged_out.check() == (
        True,
        False,
        "codex_not_authenticated",
    )


def test_settings_load_ha_connection_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("HA_MCP_URL", "http://ha.local:8123/api/mcp")
    monkeypatch.setenv("HA_TOKEN", "top-secret")
    monkeypatch.setenv("HA_MCP_TIMEOUT_SECONDS", "12")

    settings = AppSettings(_env_file=None)

    assert settings.ha_mcp_url == "http://ha.local:8123/api/mcp"
    assert settings.ha_token is not None
    assert settings.ha_token.get_secret_value() == "top-secret"
    assert settings.ha_mcp_timeout_seconds == 12


def test_default_runtime_builds_without_ha_credentials(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        ha_token=None,
        frontend_dist=tmp_path / "missing-dist",
    )

    app = build_app(settings)

    route_paths = {route.path for route in app.routes}
    assert "/api/commands" in route_paths
    assert "/api/health" in route_paths


def test_runtime_serves_built_frontend(tmp_path: Path) -> None:
    frontend_dist = tmp_path / "dist"
    assets_dir = frontend_dist / "assets"
    assets_dir.mkdir(parents=True)
    (frontend_dist / "index.html").write_text(
        "<html><body>Home Assist Agent</body></html>",
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text(
        "console.log('ready')",
        encoding="utf-8",
    )
    settings = AppSettings(
        _env_file=None,
        ha_token=None,
        frontend_dist=frontend_dist,
    )

    client = TestClient(build_app(settings))

    index_response = client.get("/")
    asset_response = client.get("/assets/app.js")
    assert index_response.status_code == 200
    assert "Home Assist Agent" in index_response.text
    assert asset_response.status_code == 200
    assert "console.log" in asset_response.text


def test_asgi_module_exposes_application() -> None:
    module = import_module("home_assist_agent.main")

    assert module.app.title == "Home Assist Agent"


def test_cli_starts_server_with_configured_binding(monkeypatch) -> None:
    cli = import_module("home_assist_agent.__main__")
    settings = AppSettings(
        _env_file=None,
        host="127.0.0.2",
        port=8765,
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(cli, "AppSettings", lambda: settings)
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **kwargs: calls.append((app, kwargs)),
    )

    cli.main()

    assert calls == [
        (
            "home_assist_agent.main:app",
            {"host": "127.0.0.2", "port": 8765},
        )
    ]

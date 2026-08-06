from dataclasses import dataclass
from importlib import import_module
import os
from pathlib import Path
import subprocess
import sys

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
    monkeypatch.setenv("HA_BASE_URL", "http://ha.local:8123")
    monkeypatch.setenv("HA_TOKEN", "top-secret")
    monkeypatch.setenv("HA_MCP_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("AUDIT_DB_PATH", "runtime/audit.db")
    monkeypatch.setenv("EVENT_DB_PATH", "runtime/events.db")
    monkeypatch.setenv("TERM_DB_PATH", "runtime/terms.db")
    monkeypatch.setenv("CONVERSATION_DB_PATH", "runtime/conversations.db")
    monkeypatch.setenv("HOME_ID", "home-1")
    monkeypatch.setenv("PERSON_ID", "person-1")
    monkeypatch.setenv("TARGET_RESOLUTION_ENABLED", "true")
    monkeypatch.setenv("TARGET_RESOLUTION_CONFIDENCE", "0.85")
    monkeypatch.setenv("TARGET_CANDIDATE_LIMIT", "12")
    monkeypatch.setenv("TERM_PROVISIONAL_SECONDS", "600")

    settings = AppSettings(_env_file=None)

    assert settings.ha_mcp_url == "http://ha.local:8123/api/mcp"
    assert settings.ha_base_url == "http://ha.local:8123"
    assert settings.ha_token is not None
    assert settings.ha_token.get_secret_value() == "top-secret"
    assert settings.ha_mcp_timeout_seconds == 12
    assert settings.audit_db_path == Path("runtime/audit.db")
    assert settings.event_db_path == Path("runtime/events.db")
    assert settings.term_db_path == Path("runtime/terms.db")
    assert settings.conversation_db_path == Path("runtime/conversations.db")
    assert settings.home_id == "home-1"
    assert settings.person_id == "person-1"
    assert settings.target_resolution_enabled is True
    assert settings.target_resolution_confidence == 0.85
    assert settings.target_candidate_limit == 12
    assert settings.term_provisional_seconds == 600


def test_default_runtime_builds_without_ha_credentials(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        ha_token=None,
        frontend_dist=tmp_path / "missing-dist",
        audit_db_path=tmp_path / "audit.db",
        event_db_path=tmp_path / "events.db",
        term_db_path=tmp_path / "terms.db",
        conversation_db_path=tmp_path / "conversations.db",
    )

    app = build_app(settings)

    route_paths = {route.path for route in app.routes}
    assert "/api/commands" in route_paths
    assert "/api/conversations/current" in route_paths
    assert "/api/conversations" in route_paths
    assert "/api/events" in route_paths
    assert "/api/health" in route_paths
    assert app.state.target_resolution_enabled is True
    assert app.state.term_promotion_worker is not None
    current = TestClient(app).get("/api/conversations/current")
    assert current.status_code == 200
    assert current.json()["messages"] == []


def test_runtime_feature_flag_off_keeps_compatibility_path(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        _env_file=None,
        ha_token=None,
        target_resolution_enabled=False,
        frontend_dist=tmp_path / "missing-dist",
        audit_db_path=tmp_path / "audit.db",
        event_db_path=tmp_path / "events.db",
        term_db_path=tmp_path / "terms.db",
    )

    app = build_app(settings)

    assert app.state.target_resolution_enabled is False
    assert app.state.term_promotion_worker is None


def test_runtime_allows_only_the_configured_unified_frontend_origin(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        _env_file=None,
        ha_token=None,
        target_resolution_enabled=False,
        frontend_dist=tmp_path / "missing-dist",
        audit_db_path=tmp_path / "audit.db",
        event_db_path=tmp_path / "events.db",
        term_db_path=tmp_path / "terms.db",
        cors_origins=["http://127.0.0.1:8090"],
    )
    client = TestClient(build_app(settings))

    allowed = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:8090",
            "Access-Control-Request-Method": "GET",
        },
    )
    blocked = client.options(
        "/api/health",
        headers={
            "Origin": "https://example.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers.get("access-control-allow-origin") == (
        "http://127.0.0.1:8090"
    )
    assert blocked.headers.get("access-control-allow-origin") is None


def test_runtime_is_api_only_when_no_legacy_frontend_is_configured(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        _env_file=None,
        ha_token=None,
        target_resolution_enabled=False,
        audit_db_path=tmp_path / "audit.db",
        event_db_path=tmp_path / "events.db",
        term_db_path=tmp_path / "terms.db",
    )

    response = TestClient(build_app(settings)).get("/")

    assert response.status_code == 404


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
        audit_db_path=tmp_path / "audit.db",
        event_db_path=tmp_path / "events.db",
        term_db_path=tmp_path / "terms.db",
    )

    client = TestClient(build_app(settings))

    index_response = client.get("/")
    audit_response = client.get("/audit")
    asset_response = client.get("/assets/app.js")
    assert index_response.status_code == 200
    assert audit_response.status_code == 200
    assert "Home Assist Agent" in index_response.text
    assert "Home Assist Agent" in audit_response.text
    assert asset_response.status_code == 200
    assert "console.log" in asset_response.text


def test_asgi_module_exposes_application() -> None:
    module = import_module("home_assist_agent.main")

    assert module.app.title == "Home Assist Agent"


def test_catalog_module_imports_in_a_fresh_process() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repository_root / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import home_assist_agent.ha.catalog",
        ],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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

    cli.main([])

    assert calls == [
        (
            "home_assist_agent.main:app",
            {"host": "127.0.0.2", "port": 8765},
        )
    ]


def test_cli_help_exits_without_starting_server(
    monkeypatch,
    capsys,
) -> None:
    cli = import_module("home_assist_agent.__main__")
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **kwargs: calls.append((app, kwargs)),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    assert "Home Assist Agent" in capsys.readouterr().out
    assert calls == []

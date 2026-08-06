import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import time

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPOSITORY_ROOT / "scripts" / "dev_all.py"
launcher_namespace = runpy.run_path(str(LAUNCHER))
build_service_specs = launcher_namespace["build_service_specs"]


def test_launcher_describes_three_independent_local_ports_without_credentials() -> None:
    environment = dict(os.environ)
    environment.update(
        {
            "HA_BASE_URL": "http://127.0.0.1:8123",
            "HA_TOKEN": "launcher-secret-must-not-leak",
        }
    )

    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "--describe"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "统一前端" in result.stdout
    assert "http://127.0.0.1:5173" in result.stdout
    assert "主流程接口" in result.stdout
    assert "http://127.0.0.1:8080" in result.stdout
    assert "IoT 接口" in result.stdout
    assert "http://127.0.0.1:8090" in result.stdout
    assert "launcher-secret-must-not-leak" not in result.stdout
    assert "launcher-secret-must-not-leak" not in result.stderr


def test_launcher_can_move_only_the_frontend_when_its_default_port_is_busy() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(LAUNCHER),
            "--describe",
            "--frontend-port",
            "5174",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "http://127.0.0.1:5174" in result.stdout
    assert "http://127.0.0.1:8080" in result.stdout
    assert "http://127.0.0.1:8090" in result.stdout


def test_launcher_maps_root_ha_configuration_only_into_child_environments() -> None:
    specs = build_service_specs(
        REPOSITORY_ROOT,
        {
            "PATH": os.environ.get("PATH", ""),
            "HA_BASE_URL": "http://127.0.0.1:8123",
            "HA_TOKEN": "ha-secret",
        },
    )
    iot = next(spec for spec in specs if spec.name == "IoT 接口")
    agent = next(spec for spec in specs if spec.name == "主流程接口")

    assert agent.environment["HA_BASE_URL"] == "http://127.0.0.1:8123"
    assert agent.environment["HA_TOKEN"] == "ha-secret"
    assert iot.environment["IOT_MCP_HOME_ASSISTANT_URL"] == (
        "http://127.0.0.1:8123"
    )
    assert iot.environment["IOT_MCP_HOME_ASSISTANT_TOKEN"] == "ha-secret"
    assert iot.environment["IOT_MCP_MOCK_PROVIDER_ENABLED"] == "false"
    assert iot.environment["IOT_MCP_AUTH_ENABLED"] == "false"
    assert iot.environment["IOT_MCP_DATABASE_URL"].endswith(
        "/data/iot_mcp.db"
    )
    assert iot.environment["IOT_MCP_AUDIT_DATABASE_PATH"].endswith(
        "/data/iot_mcp_audit.db"
    )


def test_launcher_uses_the_existing_iot_backend_virtual_environment() -> None:
    specs = build_service_specs(REPOSITORY_ROOT, {"PATH": ""})
    iot = next(spec for spec in specs if spec.name == "IoT 接口")

    assert iot.command[:3] == (
        str(
            REPOSITORY_ROOT
            / "modules"
            / "iot-mcp"
            / "backend"
            / ".venv"
            / "bin"
            / "python"
        ),
        "-m",
        "iot_mcp",
    )


def test_launcher_reads_the_local_dotenv_without_overriding_process_values(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "HA_BASE_URL=http://127.0.0.1:8123\n"
        "HA_TOKEN=file-secret\n"
        "HOME_ID=dotenv-home\n",
        encoding="utf-8",
    )
    load_environment = launcher_namespace.get("load_environment")

    assert callable(load_environment)
    environment = load_environment(
        tmp_path,
        {"HA_TOKEN": "process-secret", "PATH": "/usr/bin"},
    )

    assert environment["HA_BASE_URL"] == "http://127.0.0.1:8123"
    assert environment["HA_TOKEN"] == "process-secret"
    assert environment["HOME_ID"] == "dotenv-home"


def test_launcher_detects_an_occupied_local_port() -> None:
    find_port_conflicts = launcher_namespace.get("find_port_conflicts")
    assert callable(find_port_conflicts)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied_port = listener.getsockname()[1]

        assert find_port_conflicts("127.0.0.1", [occupied_port]) == [
            occupied_port
        ]


def test_launcher_stops_peer_process_when_one_service_exits(tmp_path: Path) -> None:
    run_services = launcher_namespace.get("run_services")
    service_spec = launcher_namespace["ServiceSpec"]
    assert callable(run_services)
    pid_file = tmp_path / "peer.pid"
    peer = service_spec(
        name="peer",
        address="local",
        command=(
            sys.executable,
            "-c",
            (
                "import os,pathlib,time;"
                f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
                "time.sleep(30)"
            ),
        ),
        cwd=tmp_path,
        environment=os.environ,
    )
    failing = service_spec(
        name="failing",
        address="local",
        command=(sys.executable, "-c", "import time;time.sleep(.3);raise SystemExit(7)"),
        cwd=tmp_path,
        environment=os.environ,
    )

    result = run_services((peer, failing), poll_interval=0.02)

    assert result == 7
    for _ in range(50):
        if pid_file.exists():
            break
        time.sleep(0.01)
    peer_pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(peer_pid, 0)


def test_launcher_preflight_reports_missing_runtime_requirements(
    tmp_path: Path,
) -> None:
    missing_requirements = launcher_namespace.get("missing_requirements")
    service_spec = launcher_namespace["ServiceSpec"]
    assert callable(missing_requirements)
    spec = service_spec(
        name="missing-service",
        address="local",
        command=(str(tmp_path / "missing-python"), "-m", "service"),
        cwd=tmp_path / "missing-workdir",
        environment={"PATH": ""},
    )

    problems = missing_requirements((spec,))

    assert problems == [
        f"missing-service 工作目录不存在：{tmp_path / 'missing-workdir'}",
        f"missing-service 缺少可执行文件：{tmp_path / 'missing-python'}",
    ]

#!/usr/bin/env python3
"""Run the unified Web console and both independent local backends."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Mapping, Sequence

from dotenv import dotenv_values


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    address: str
    command: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]


def load_environment(
    repository_root: Path,
    process_environment: Mapping[str, str],
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in dotenv_values(repository_root / ".env").items()
        if value is not None
    }
    environment.update(process_environment)
    return environment


def find_port_conflicts(host: str, ports: Sequence[int]) -> list[int]:
    conflicts: list[int] = []
    for port in ports:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex((host, port)) == 0:
                conflicts.append(port)
    return conflicts


def missing_requirements(specs: Sequence[ServiceSpec]) -> list[str]:
    missing: list[str] = []
    for spec in specs:
        if not spec.cwd.is_dir():
            missing.append(f"{spec.name} 工作目录不存在：{spec.cwd}")
        executable = spec.command[0]
        if Path(executable).is_absolute():
            if not Path(executable).is_file():
                missing.append(f"{spec.name} 缺少可执行文件：{executable}")
        elif shutil.which(executable, path=spec.environment.get("PATH")) is None:
            missing.append(f"{spec.name} 缺少命令：{executable}")
    return missing


def run_services(
    specs: Sequence[ServiceSpec],
    *,
    poll_interval: float = 0.1,
) -> int:
    processes: list[tuple[ServiceSpec, subprocess.Popen[bytes]]] = []
    exit_code = 0
    try:
        for spec in specs:
            process = subprocess.Popen(
                spec.command,
                cwd=spec.cwd,
                env=dict(spec.environment),
                start_new_session=True,
            )
            processes.append((spec, process))
            print(f"已启动 {spec.name}：{spec.address}", flush=True)

        while True:
            for spec, process in processes:
                result = process.poll()
                if result is None:
                    continue
                exit_code = result if result != 0 else 1
                print(
                    f"{spec.name} 已退出（code={result}），正在停止其他服务。",
                    file=sys.stderr,
                    flush=True,
                )
                return exit_code
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("收到停止信号，正在关闭全部服务。", flush=True)
        return 0
    finally:
        for _, process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for _, process in processes:
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)


def build_service_specs(
    repository_root: Path,
    environment: Mapping[str, str],
    *,
    frontend_port: int = 5173,
) -> tuple[ServiceSpec, ...]:
    web_environment = dict(environment)
    agent_environment = dict(environment)
    agent_environment.update({"HOST": "127.0.0.1", "PORT": "8080"})
    iot_environment = dict(environment)
    iot_environment.update(
        {
            "IOT_MCP_SERVER_HOST": "127.0.0.1",
            "IOT_MCP_SERVER_PORT": "8090",
            "IOT_MCP_AUTH_ENABLED": "false",
            "IOT_MCP_DATABASE_URL": (
                "sqlite+aiosqlite:///"
                f"{repository_root / 'data' / 'iot_mcp.db'}"
            ),
            "IOT_MCP_AUDIT_DATABASE_PATH": str(
                repository_root / "data" / "iot_mcp_audit.db"
            ),
        }
    )
    ha_url = environment.get("HA_BASE_URL", "").strip()
    ha_token = environment.get("HA_TOKEN", "").strip()
    if ha_url and ha_token:
        iot_environment.update(
            {
                "IOT_MCP_HOME_ASSISTANT_URL": ha_url,
                "IOT_MCP_HOME_ASSISTANT_TOKEN": ha_token,
                "IOT_MCP_MOCK_PROVIDER_ENABLED": "false",
            }
        )
    return (
        ServiceSpec(
            name="统一前端",
            address=f"http://127.0.0.1:{frontend_port}",
            command=(
                "npm",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(frontend_port),
            ),
            cwd=repository_root / "modules" / "iot-mcp" / "web",
            environment=web_environment,
        ),
        ServiceSpec(
            name="主流程接口",
            address="http://127.0.0.1:8080",
            command=(
                str(repository_root / ".venv" / "bin" / "python"),
                "-m",
                "home_assist_agent",
            ),
            cwd=repository_root,
            environment=agent_environment,
        ),
        ServiceSpec(
            name="IoT 接口",
            address="http://127.0.0.1:8090",
            command=(
                str(
                    repository_root
                    / "modules"
                    / "iot-mcp"
                    / "backend"
                    / ".venv"
                    / "bin"
                    / "python"
                ),
                "-m",
                "iot_mcp",
                "--mode",
                "http",
            ),
            cwd=repository_root / "modules" / "iot-mcp" / "backend",
            environment=iot_environment,
        ),
    )


def describe(specs: Sequence[ServiceSpec]) -> None:
    label_width = max(len(spec.name) for spec in specs)
    for spec in specs:
        print(f"{spec.name:<{label_width}} {spec.address}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--describe",
        action="store_true",
        help="只显示本地入口，不启动进程",
    )
    parser.add_argument(
        "--frontend-port",
        type=int,
        default=5173,
        help="统一前端端口（默认 5173）",
    )
    args = parser.parse_args(argv)
    environment = load_environment(REPOSITORY_ROOT, os.environ)
    specs = build_service_specs(
        REPOSITORY_ROOT,
        environment,
        frontend_port=args.frontend_port,
    )
    if args.describe:
        describe(specs)
        return 0
    missing = missing_requirements(specs)
    if missing:
        for problem in missing:
            print(problem, file=sys.stderr)
        return 2
    conflicts = find_port_conflicts(
        "127.0.0.1",
        (args.frontend_port, 8080, 8090),
    )
    if conflicts:
        ports = ", ".join(str(port) for port in conflicts)
        print(f"端口已被占用：{ports}", file=sys.stderr)
        return 2
    (REPOSITORY_ROOT / "data").mkdir(parents=True, exist_ok=True)
    describe(specs)
    return run_services(specs)


if __name__ == "__main__":
    raise SystemExit(main())

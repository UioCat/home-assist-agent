"""CLI entrypoints for the standalone HTTP API and MCP transports."""

from __future__ import annotations

import argparse

import uvicorn

from iot_mcp.bootstrap.runtime import build_runtime


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="iot-mcp")
    parser.add_argument("--mode", choices=("http", "mcp"), default="http")
    parser.add_argument(
        "--mcp-transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP uses stdio by default; streamable-http listens on IOT_MCP_MCP_HOST/PORT.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    runtime = build_runtime()
    if args.mode == "http":
        uvicorn.run(
            runtime.http_app,
            host=runtime.container.settings.server_host,
            port=runtime.container.settings.server_port,
        )
        return
    runtime.mcp_server.run(transport=args.mcp_transport)


if __name__ == "__main__":
    main()

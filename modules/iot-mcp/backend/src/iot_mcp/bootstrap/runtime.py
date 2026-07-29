"""Runnable application runtime for either HTTP or MCP transport."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from iot_mcp.adapters.inbound.http.app import create_app
from iot_mcp.adapters.inbound.mcp.server import create_mcp_server
from iot_mcp.bootstrap.container import ApplicationContainer, create_container
from iot_mcp.config.settings import Settings
from iot_mcp.ports.device_provider import DeviceProvider


class Runtime:
    def __init__(self, container: ApplicationContainer) -> None:
        self.container = container
        self.http_app: FastAPI = create_app(container=container)
        self.mcp_server: FastMCP = create_mcp_server(container, lifespan=self.mcp_lifespan)
        self._started = False

    async def startup(self) -> None:
        if not self._started:
            await self.container.startup()
            self._started = True

    async def shutdown(self) -> None:
        if self._started:
            await self.container.shutdown()
            self._started = False

    @asynccontextmanager
    async def mcp_lifespan(self, _: FastMCP):
        await self.startup()
        try:
            yield self
        finally:
            await self.shutdown()


def build_runtime(
    settings: Settings | None = None,
    *,
    providers: dict[str, DeviceProvider] | None = None,
) -> Runtime:
    return Runtime(create_container(settings, providers=providers))

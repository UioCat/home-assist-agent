from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
import json
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ListToolsResult

from home_assist_agent.commands.models import (
    ToolDefinition,
    ToolExecutionResult,
)
from home_assist_agent.errors import DependencyError


class McpSessionProtocol(Protocol):
    async def list_tools(self, cursor: str | None = None) -> ListToolsResult: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult: ...


SessionFactory = Callable[
    [str, str, float],
    Any,
]


class HomeAssistantMcpClient:
    def __init__(
        self,
        url: str,
        token: str | None,
        timeout_seconds: float = 20,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._session_factory = session_factory or self._open_session

    async def list_tools(self) -> list[ToolDefinition]:
        self._require_token()
        try:
            async with self._session_factory(
                self._url,
                self._token,
                self._timeout_seconds,
            ) as session:
                tools: list[ToolDefinition] = []
                cursor: str | None = None
                while True:
                    result = await session.list_tools(cursor=cursor)
                    tools.extend(
                        ToolDefinition(
                            name=tool.name,
                            description=tool.description or "",
                            input_schema=tool.inputSchema,
                        )
                        for tool in result.tools
                    )
                    cursor = result.nextCursor
                    if cursor is None:
                        return tools
        except DependencyError:
            raise
        except Exception as error:
            raise self._map_error(error) from error

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        self._require_token()
        try:
            async with self._session_factory(
                self._url,
                self._token,
                self._timeout_seconds,
            ) as session:
                result = await session.call_tool(name, arguments)
        except DependencyError:
            raise
        except Exception as error:
            raise self._map_error(error) from error

        content = self._content_text(result)
        if result.isError:
            raise DependencyError(
                "ha_tool_failed",
                content or "Home Assistant MCP 工具执行失败。",
            )
        return ToolExecutionResult(
            tool_name=name,
            content=content or "Home Assistant 已接受该工具调用。",
        )

    def _require_token(self) -> None:
        if not self._token:
            raise DependencyError(
                "ha_not_configured",
                "尚未配置 Home Assistant MCP Token。",
            )

    @staticmethod
    def _content_text(result: CallToolResult) -> str:
        text_parts = [
            item.text
            for item in result.content
            if getattr(item, "type", None) == "text"
        ]
        if text_parts:
            return "\n".join(text_parts)
        if result.structuredContent is not None:
            return json.dumps(result.structuredContent, ensure_ascii=False)
        return ""

    @classmethod
    def _map_error(cls, error: Exception) -> DependencyError:
        root = cls._find_http_error(error)
        if isinstance(root, httpx.HTTPStatusError):
            status_code = root.response.status_code
            if status_code == 401:
                return DependencyError(
                    "ha_unauthorized",
                    "Home Assistant MCP Token 无效。",
                )
            if status_code == 404:
                return DependencyError(
                    "ha_mcp_not_enabled",
                    "Home Assistant 尚未启用 MCP Server 集成。",
                )
        if isinstance(root, (httpx.TimeoutException, httpx.HTTPError)):
            return DependencyError(
                "ha_unavailable",
                "无法连接 Home Assistant MCP。",
            )
        return DependencyError(
            "ha_unavailable",
            "Home Assistant MCP 请求失败。",
        )

    @classmethod
    def _find_http_error(cls, error: Exception) -> Exception:
        if isinstance(error, BaseExceptionGroup):
            for nested in error.exceptions:
                if isinstance(nested, Exception):
                    candidate = cls._find_http_error(nested)
                    if isinstance(candidate, httpx.HTTPError):
                        return candidate
        return error

    @staticmethod
    @asynccontextmanager
    async def _open_session(
        url: str,
        token: str,
        timeout_seconds: float,
    ) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout_seconds,
        ) as http_client:
            async with streamable_http_client(
                url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=timeout_seconds),
                ) as session:
                    await session.initialize()
                    yield session

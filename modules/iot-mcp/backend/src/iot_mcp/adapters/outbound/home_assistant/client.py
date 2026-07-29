"""Thin, injectable REST client for Home Assistant's native API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import websockets

RegistryLoader = Callable[[], Awaitable[dict[str, str | None]]]


class HomeAssistantError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class HomeAssistantClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 10,
        client: httpx.AsyncClient | None = None,
        registry_loader: RegistryLoader | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout_seconds = timeout_seconds
        self._registry_loader = registry_loader

    async def aclose(self) -> None:
        await self._client.aclose()

    async def websocket_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield HA WebSocket event messages after the native auth handshake."""
        websocket_url = self._base_url.replace("https://", "wss://").replace("http://", "ws://")
        async with websockets.connect(
            f"{websocket_url}/api/websocket", open_timeout=self._timeout_seconds
        ) as socket:
            required = json.loads(await socket.recv())
            if required.get("type") != "auth_required":
                raise HomeAssistantError("provider_error", "expected HA WebSocket auth_required")
            await socket.send(json.dumps({"type": "auth", "access_token": self._token}))
            authenticated = json.loads(await socket.recv())
            if authenticated.get("type") != "auth_ok":
                raise HomeAssistantError(
                    "provider_auth_error", "HA WebSocket authentication failed"
                )
            await socket.send(
                json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
            )
            subscribed = json.loads(await socket.recv())
            if not subscribed.get("success"):
                raise HomeAssistantError("provider_error", "HA WebSocket subscription failed")
            while True:
                yield json.loads(await socket.recv())

    async def get_entity_device_ids(self) -> dict[str, str | None]:
        """Read the HA entity registry so physical devices do not become virtual entities."""
        if self._registry_loader is not None:
            return await self._registry_loader()
        entries = await self._websocket_command("config/entity_registry/list")
        if not isinstance(entries, list):
            raise HomeAssistantError("provider_error", "HA entity registry must be an array")
        return {
            entry["entity_id"]: entry.get("device_id")
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("entity_id"), str)
        }

    async def get_states(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/api/states")
        body = self._decode_json(response)
        if not isinstance(body, list):
            raise HomeAssistantError("provider_error", "HA states response must be an array")
        return body

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/api/states/{entity_id}")
        body = self._decode_json(response)
        if not isinstance(body, dict):
            raise HomeAssistantError("provider_error", "HA state response must be an object")
        return body

    async def call_service(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        response = await self._request("POST", f"/api/services/{domain}/{service}", json=data)
        return self._decode_json(response)

    def _decode_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise HomeAssistantError("provider_error", "HA returned invalid JSON") from error

    async def _websocket_command(self, command_type: str) -> Any:
        websocket_url = self._base_url.replace("https://", "wss://").replace("http://", "ws://")
        try:
            async with websockets.connect(
                f"{websocket_url}/api/websocket", open_timeout=self._timeout_seconds
            ) as socket:
                required = json.loads(await socket.recv())
                if required.get("type") != "auth_required":
                    raise HomeAssistantError(
                        "provider_error", "expected HA WebSocket auth_required"
                    )
                await socket.send(json.dumps({"type": "auth", "access_token": self._token}))
                authenticated = json.loads(await socket.recv())
                if authenticated.get("type") != "auth_ok":
                    raise HomeAssistantError(
                        "provider_auth_error", "HA WebSocket authentication failed"
                    )
                await socket.send(json.dumps({"id": 1, "type": command_type}))
                result = json.loads(await socket.recv())
        except HomeAssistantError:
            raise
        except (OSError, ValueError, websockets.WebSocketException) as error:
            raise HomeAssistantError("provider_offline", str(error)) from error
        if not result.get("success"):
            raise HomeAssistantError(
                "provider_error", f"HA WebSocket command failed: {command_type}"
            )
        return result.get("result")

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers,
                timeout=self._timeout_seconds,
                **kwargs,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise HomeAssistantError("provider_offline", str(error)) from error
        if response.status_code in {401, 403}:
            raise HomeAssistantError("provider_auth_error", response.text)
        if response.status_code == 404:
            raise HomeAssistantError("target_not_found", response.text)
        if response.is_error:
            raise HomeAssistantError("provider_error", response.text)
        return response

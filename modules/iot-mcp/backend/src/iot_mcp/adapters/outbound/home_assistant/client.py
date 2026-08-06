"""Thin, injectable REST client for Home Assistant's native API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import websockets

from iot_mcp.audit import AuditRecorder

RegistryLoader = Callable[[], Awaitable[Any]]


class HomeAssistantError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class HomeAssistantTimeout(TimeoutError):
    """A request may have reached HA, so the operation outcome is unknown."""

    category = "provider_timeout"


class HomeAssistantClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 10,
        client: httpx.AsyncClient | None = None,
        registry_loader: RegistryLoader | None = None,
        audit: AuditRecorder,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout_seconds = timeout_seconds
        self._registry_loader = registry_loader
        self._loaded_registry: Any = None
        self._audit = audit

    async def aclose(self) -> None:
        await self._client.aclose()

    async def websocket_events(self, *, message_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield HA WebSocket event messages after the native auth handshake."""
        websocket_url = self._base_url.replace("https://", "wss://").replace("http://", "ws://")
        await self._audit.record(
            message_id=message_id,
            event_type="external.request",
            service="home_assistant",
            payload={"transport": "websocket", "operation": "subscribe_events"},
        )
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
            await self._audit.record(
                message_id=message_id,
                event_type="external.response",
                service="home_assistant",
                payload={
                    "transport": "websocket",
                    "operation": "subscribe_events",
                    "body": subscribed,
                },
            )
            while True:
                yield json.loads(await socket.recv())

    async def get_entity_device_ids(self, *, message_id: str) -> dict[str, str | None]:
        """Read the HA entity registry so physical devices do not become virtual entities."""
        entries = await self.get_entity_registry(message_id=message_id)
        return {
            entry["entity_id"]: entry.get("device_id")
            for entry in entries
            if isinstance(entry.get("entity_id"), str)
        }

    async def get_entity_registry(self, *, message_id: str) -> list[dict[str, Any]]:
        loaded = await self._load_injected_registry()
        if loaded is not None:
            if isinstance(loaded, list):
                entries = loaded
            elif isinstance(loaded, dict) and isinstance(loaded.get("entities"), list):
                entries = loaded["entities"]
            elif isinstance(loaded, dict):
                entries = [
                    {
                        "entity_id": entity_id,
                        "device_id": device_id,
                        "id": entity_id,
                    }
                    for entity_id, device_id in loaded.items()
                ]
            else:
                entries = []
        else:
            entries = await self._websocket_command(
                "config/entity_registry/list", message_id=message_id
            )
        if not isinstance(entries, list):
            raise HomeAssistantError("provider_error", "HA entity registry must be an array")
        return [entry for entry in entries if isinstance(entry, dict)]

    async def get_device_registry(self, *, message_id: str) -> list[dict[str, Any]]:
        loaded = await self._load_injected_registry()
        if loaded is not None:
            entries = loaded.get("devices", []) if isinstance(loaded, dict) else []
        else:
            entries = await self._websocket_command(
                "config/device_registry/list", message_id=message_id
            )
        if not isinstance(entries, list):
            raise HomeAssistantError("provider_error", "HA device registry must be an array")
        return [entry for entry in entries if isinstance(entry, dict)]

    async def get_area_registry(self, *, message_id: str) -> list[dict[str, Any]]:
        loaded = await self._load_injected_registry()
        if loaded is not None:
            entries = loaded.get("areas", []) if isinstance(loaded, dict) else []
        else:
            entries = await self._websocket_command(
                "config/area_registry/list", message_id=message_id
            )
        if not isinstance(entries, list):
            raise HomeAssistantError("provider_error", "HA area registry must be an array")
        return [entry for entry in entries if isinstance(entry, dict)]

    async def _load_injected_registry(self) -> Any:
        if self._registry_loader is None:
            return None
        if self._loaded_registry is None:
            self._loaded_registry = await self._registry_loader()
        return self._loaded_registry

    async def get_config(self, *, message_id: str) -> dict[str, Any]:
        response = await self._request(message_id, "GET", "/api/config")
        body = self._decode_json(response)
        if not isinstance(body, dict):
            raise HomeAssistantError("provider_error", "HA config response must be an object")
        return body

    async def get_services(self, *, message_id: str) -> list[dict[str, Any]]:
        response = await self._request(message_id, "GET", "/api/services")
        body = self._decode_json(response)
        if not isinstance(body, list):
            raise HomeAssistantError("provider_error", "HA services response must be an array")
        return [item for item in body if isinstance(item, dict)]

    async def get_states(self, *, message_id: str) -> list[dict[str, Any]]:
        response = await self._request(message_id, "GET", "/api/states")
        body = self._decode_json(response)
        if not isinstance(body, list):
            raise HomeAssistantError("provider_error", "HA states response must be an array")
        return body

    async def get_state(self, entity_id: str, *, message_id: str) -> dict[str, Any]:
        response = await self._request(message_id, "GET", f"/api/states/{entity_id}")
        body = self._decode_json(response)
        if not isinstance(body, dict):
            raise HomeAssistantError("provider_error", "HA state response must be an object")
        return body

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        message_id: str,
    ) -> Any:
        response = await self._request(
            message_id,
            "POST",
            f"/api/services/{domain}/{service}",
            json=data,
            indeterminate_timeout=True,
        )
        return self._decode_json(response)

    def _decode_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise HomeAssistantError("provider_error", "HA returned invalid JSON") from error

    async def _websocket_command(self, command_type: str, *, message_id: str) -> Any:
        websocket_url = self._base_url.replace("https://", "wss://").replace("http://", "ws://")
        await self._audit.record(
            message_id=message_id,
            event_type="external.request",
            service="home_assistant",
            payload={"transport": "websocket", "command": command_type},
        )
        result: dict[str, Any] | None = None
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
        except HomeAssistantError as error:
            await self._audit.record(
                message_id=message_id,
                event_type="external.response",
                service="home_assistant",
                payload={"transport": "websocket", "command": command_type, "error": str(error)},
                status="error",
                error_code=error.category,
            )
            raise
        except (OSError, ValueError, websockets.WebSocketException) as error:
            mapped = HomeAssistantError("provider_offline", str(error))
            await self._audit.record(
                message_id=message_id,
                event_type="external.response",
                service="home_assistant",
                payload={"transport": "websocket", "command": command_type, "error": str(mapped)},
                status="error",
                error_code=mapped.category,
            )
            raise mapped from error
        assert result is not None
        if not result.get("success"):
            error = HomeAssistantError(
                "provider_error", f"HA WebSocket command failed: {command_type}"
            )
            await self._audit.record(
                message_id=message_id,
                event_type="external.response",
                service="home_assistant",
                payload={"transport": "websocket", "command": command_type, "body": result},
                status="error",
                error_code=error.category,
            )
            raise error
        body = result.get("result")
        await self._audit.record(
            message_id=message_id,
            event_type="external.response",
            service="home_assistant",
            payload={"transport": "websocket", "command": command_type, "body": body},
        )
        return body

    async def _request(
        self, message_id: str, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        indeterminate_timeout = bool(kwargs.pop("indeterminate_timeout", False))
        await self._audit.record(
            message_id=message_id,
            event_type="external.request",
            service="home_assistant",
            payload={"method": method, "path": path, "json": kwargs.get("json")},
        )
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers,
                timeout=self._timeout_seconds,
                **kwargs,
            )
        except httpx.TimeoutException as error:
            mapped: HomeAssistantError | HomeAssistantTimeout
            if indeterminate_timeout:
                mapped = HomeAssistantTimeout(str(error))
            else:
                mapped = HomeAssistantError("provider_offline", str(error))
            await self._record_http_error(message_id, method, path, mapped)
            raise mapped from error
        except httpx.TransportError as error:
            mapped = HomeAssistantError("provider_offline", str(error))
            await self._record_http_error(message_id, method, path, mapped)
            raise mapped from error
        body = self._response_body(response)
        if response.status_code in {401, 403}:
            error = HomeAssistantError("provider_auth_error", response.text)
            await self._record_http_response(message_id, method, path, response, body, error)
            raise error
        if response.status_code == 404:
            error = HomeAssistantError("target_not_found", response.text)
            await self._record_http_response(message_id, method, path, response, body, error)
            raise error
        if response.is_error:
            error = HomeAssistantError("provider_error", response.text)
            await self._record_http_response(message_id, method, path, response, body, error)
            raise error
        await self._record_http_response(message_id, method, path, response, body)
        return response

    async def _record_http_error(
        self,
        message_id: str,
        method: str,
        path: str,
        error: HomeAssistantError | HomeAssistantTimeout,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            event_type="external.response",
            service="home_assistant",
            payload={"method": method, "path": path, "error": str(error)},
            status="error",
            error_code=error.category,
        )

    async def _record_http_response(
        self,
        message_id: str,
        method: str,
        path: str,
        response: httpx.Response,
        body: Any,
        error: HomeAssistantError | None = None,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            event_type="external.response",
            service="home_assistant",
            payload={
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "body": body,
            },
            status="error" if error else "success",
            error_code=error.category if error else None,
        )

    @staticmethod
    def _response_body(response: httpx.Response) -> Any:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return response.text

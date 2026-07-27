from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect

from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import (
    ActorContext,
    CatalogSnapshot,
    HaEntitySnapshot,
)


class RegistrySocketProtocol(Protocol):
    async def send(self, value: str) -> None: ...

    async def recv(self) -> str | bytes: ...


RegistrySocketFactory = Callable[[str, str, float], Any]


class HomeAssistantCatalogProvider(Protocol):
    async def snapshot(
        self,
        actor: ActorContext,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> CatalogSnapshot: ...


_REGISTRY_OPERATIONS = (
    ("entity_registry", "config/entity_registry/list"),
    ("device_registry", "config/device_registry/list"),
    ("area_registry", "config/area_registry/list"),
)
_POWER_DOMAINS = frozenset(
    {
        "fan",
        "humidifier",
        "input_boolean",
        "light",
        "media_player",
        "remote",
        "switch",
    }
)


class HomeAssistantCatalogClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str | None,
        timeout_seconds: float = 10,
        http_transport: httpx.AsyncBaseTransport | None = None,
        websocket_factory: RegistrySocketFactory | None = None,
        audit: AuditRecorderProtocol | None = None,
    ) -> None:
        if audit is None:
            raise ValueError("audit recorder is required")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._http_transport = http_transport
        self._websocket_factory = websocket_factory or self._open_registry_socket
        self._audit = audit

    async def snapshot(
        self,
        actor: ActorContext,
        message_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> CatalogSnapshot:
        states = await self._load_states(
            message_id,
            correlation_id,
            causation_id,
        )
        registries = await self._load_registries(
            message_id,
            correlation_id,
            causation_id,
        )
        entities = self._merge(
            home_id=actor.home_id,
            states=states,
            entity_registry=registries["entity_registry"],
            device_registry=registries["device_registry"],
            area_registry=registries["area_registry"],
        )
        return CatalogSnapshot(
            home_id=actor.home_id,
            catalog_version=self._catalog_version(actor.home_id, entities),
            observed_at=datetime.now(UTC),
            entities=entities,
        )

    async def _load_states(
        self,
        message_id: str,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> list[dict[str, Any]]:
        operation = "states"
        url = f"{self._base_url}/api/states"
        await self._record_request(
            message_id,
            operation,
            {"method": "GET", "url": url},
            correlation_id,
            causation_id,
        )
        response_payload: Any = None
        try:
            self._require_token()
            async with httpx.AsyncClient(
                transport=self._http_transport,
                timeout=self._timeout_seconds,
                headers={"Authorization": f"Bearer {self._token}"},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                response_payload = response.json()
            result = self._require_record_list(response_payload, operation)
        except Exception as error:
            mapped = self._map_error(error)
            await self._record_error(
                message_id,
                operation,
                mapped,
                response_payload,
                correlation_id,
                causation_id,
            )
            raise mapped from error
        await self._record_response(
            message_id,
            operation,
            result,
            correlation_id,
            causation_id,
        )
        return result

    async def _load_registries(
        self,
        message_id: str,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> dict[str, list[dict[str, Any]]]:
        first_operation, first_command = _REGISTRY_OPERATIONS[0]
        await self._record_request(
            message_id,
            first_operation,
            {"transport": "websocket", "command": first_command},
            correlation_id,
            causation_id,
        )
        current_operation = first_operation
        response_payload: Any = None
        results: dict[str, list[dict[str, Any]]] = {}
        try:
            self._require_token()
            async with self._websocket_factory(
                self._websocket_url(),
                self._token,
                self._timeout_seconds,
            ) as socket:
                for index, (operation, command) in enumerate(
                    _REGISTRY_OPERATIONS,
                    start=1,
                ):
                    current_operation = operation
                    if index > 1:
                        await self._record_request(
                            message_id,
                            operation,
                            {
                                "transport": "websocket",
                                "command": command,
                            },
                            correlation_id,
                            causation_id,
                        )
                    await socket.send(
                        json.dumps(
                            {"id": index, "type": command},
                            separators=(",", ":"),
                        )
                    )
                    response_payload = self._decode_message(await socket.recv())
                    result = self._parse_registry_result(
                        response_payload,
                        expected_id=index,
                        operation=operation,
                    )
                    await self._record_response(
                        message_id,
                        operation,
                        result,
                        correlation_id,
                        causation_id,
                    )
                    results[operation] = result
                    response_payload = None
        except Exception as error:
            mapped = self._map_error(error)
            await self._record_error(
                message_id,
                current_operation,
                mapped,
                response_payload,
                correlation_id,
                causation_id,
            )
            raise mapped from error
        return results

    async def _record_request(
        self,
        message_id: str,
        operation: str,
        request: dict[str, Any],
        correlation_id: str | None,
        causation_id: str | None,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            event_type="external.request",
            service="home_assistant_catalog",
            payload={"operation": operation, "request": request},
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    async def _record_response(
        self,
        message_id: str,
        operation: str,
        response: Any,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            event_type="external.response",
            service="home_assistant_catalog",
            payload={"operation": operation, "response": response},
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    async def _record_error(
        self,
        message_id: str,
        operation: str,
        error: DependencyError,
        response: Any,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            event_type="external.response",
            service="home_assistant_catalog",
            payload={
                "operation": operation,
                "response": response,
                "error": error.message,
            },
            status="error",
            error_code=error.code,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def _require_token(self) -> None:
        if not self._token:
            raise DependencyError(
                "ha_not_configured",
                "尚未配置 Home Assistant Token。",
            )

    def _websocket_url(self) -> str:
        parsed = urlsplit(self._base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, "/api/websocket", "", ""))

    @staticmethod
    def _decode_message(value: str | bytes) -> Any:
        try:
            return json.loads(value)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DependencyError(
                "ha_invalid_response",
                "Home Assistant 返回了无效的目录响应。",
            ) from error

    @classmethod
    def _parse_registry_result(
        cls,
        payload: Any,
        *,
        expected_id: int,
        operation: str,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(payload, dict)
            or payload.get("id") != expected_id
            or payload.get("type") != "result"
            or payload.get("success") is not True
        ):
            raise DependencyError(
                "ha_invalid_response",
                f"Home Assistant {operation} 响应无效。",
            )
        return cls._require_record_list(payload.get("result"), operation)

    @staticmethod
    def _require_record_list(
        value: Any,
        operation: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(
            not isinstance(item, dict) for item in value
        ):
            raise DependencyError(
                "ha_invalid_response",
                f"Home Assistant {operation} 响应不是对象列表。",
            )
        return value

    @classmethod
    def _merge(
        cls,
        *,
        home_id: str,
        states: list[dict[str, Any]],
        entity_registry: list[dict[str, Any]],
        device_registry: list[dict[str, Any]],
        area_registry: list[dict[str, Any]],
    ) -> tuple[HaEntitySnapshot, ...]:
        state_by_id = {
            item["entity_id"]: item
            for item in states
            if isinstance(item.get("entity_id"), str)
            and "." in item["entity_id"]
        }
        registry_by_id = {
            item["entity_id"]: item
            for item in entity_registry
            if isinstance(item.get("entity_id"), str)
            and "." in item["entity_id"]
        }
        device_by_id = {
            item["id"]: item
            for item in device_registry
            if isinstance(item.get("id"), str)
        }
        area_by_id = {
            str(item.get("area_id") or item.get("id")): item
            for item in area_registry
            if item.get("area_id") or item.get("id")
        }
        entity_ids = sorted(set(state_by_id) | set(registry_by_id))
        merged: list[HaEntitySnapshot] = []
        for entity_id in entity_ids:
            state_item = state_by_id.get(entity_id, {})
            registry_item = registry_by_id.get(entity_id, {})
            attributes = state_item.get("attributes")
            if not isinstance(attributes, dict):
                attributes = {}
            device_id = cls._optional_string(registry_item.get("device_id"))
            device = device_by_id.get(device_id or "", {})
            area_id = cls._optional_string(
                registry_item.get("area_id") or device.get("area_id")
            )
            area = area_by_id.get(area_id or "", {})
            state = str(state_item.get("state") or "unavailable")
            domain = entity_id.partition(".")[0]
            capabilities = cls._capabilities(domain, attributes)
            merged.append(
                HaEntitySnapshot(
                    home_id=home_id,
                    entity_id=entity_id,
                    domain=domain,
                    device_id=device_id,
                    area_id=area_id,
                    area_name=cls._optional_string(area.get("name")),
                    floor_name=cls._optional_string(area.get("floor_name")),
                    friendly_name=cls._optional_string(
                        attributes.get("friendly_name")
                        or registry_item.get("name")
                    ),
                    original_name=cls._optional_string(
                        registry_item.get("original_name")
                    ),
                    aliases=cls._string_tuple(registry_item.get("aliases")),
                    device_name=cls._optional_string(
                        device.get("name_by_user") or device.get("name")
                    ),
                    device_aliases=cls._string_tuple(device.get("aliases")),
                    state=state,
                    attributes=attributes,
                    capabilities=capabilities,
                    available=bool(state_item)
                    and state.casefold() not in {"unavailable", "unknown"},
                    disabled=registry_item.get("disabled_by") is not None,
                )
            )
        return tuple(merged)

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value
        return None

    @staticmethod
    def _string_tuple(value: Any) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(
            sorted(
                {
                    item
                    for item in value
                    if isinstance(item, str) and item.strip()
                }
            )
        )

    @staticmethod
    def _capabilities(
        domain: str,
        attributes: dict[str, Any],
    ) -> frozenset[str]:
        capabilities: set[str] = set()
        if domain in _POWER_DOMAINS:
            capabilities.update({"turn_on", "turn_off"})
        color_modes = attributes.get("supported_color_modes")
        supports_brightness = (
            isinstance(color_modes, list)
            and any(mode != "onoff" for mode in color_modes)
        ) or "brightness" in attributes
        if domain == "light" and supports_brightness:
            capabilities.add("set_brightness")
        return frozenset(capabilities)

    @staticmethod
    def _catalog_version(
        home_id: str,
        entities: tuple[HaEntitySnapshot, ...],
    ) -> str:
        identity = {
            "home_id": home_id,
            "entities": [
                {
                    "entity_id": entity.entity_id,
                    "domain": entity.domain,
                    "device_id": entity.device_id,
                    "area_id": entity.area_id,
                    "area_name": entity.area_name,
                    "floor_name": entity.floor_name,
                    "friendly_name": entity.friendly_name,
                    "original_name": entity.original_name,
                    "aliases": entity.aliases,
                    "device_name": entity.device_name,
                    "device_aliases": entity.device_aliases,
                    "capabilities": sorted(entity.capabilities),
                    "disabled": entity.disabled,
                }
                for entity in entities
            ],
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _map_error(error: Exception) -> DependencyError:
        if isinstance(error, DependencyError):
            return error
        if isinstance(error, httpx.HTTPStatusError):
            if error.response.status_code in {401, 403}:
                return DependencyError(
                    "ha_unauthorized",
                    "Home Assistant Token 无效。",
                )
        if isinstance(error, (httpx.TimeoutException, TimeoutError)):
            return DependencyError(
                "ha_unavailable",
                "连接 Home Assistant 目录超时。",
            )
        if isinstance(error, (httpx.HTTPError, OSError)):
            return DependencyError(
                "ha_unavailable",
                "无法连接 Home Assistant 目录。",
            )
        return DependencyError(
            "ha_unavailable",
            "Home Assistant 目录请求失败。",
        )

    @staticmethod
    @asynccontextmanager
    async def _open_registry_socket(
        url: str,
        token: str,
        timeout_seconds: float,
    ) -> AsyncIterator[RegistrySocketProtocol]:
        async with connect(
            url,
            open_timeout=timeout_seconds,
            close_timeout=timeout_seconds,
        ) as socket:
            auth_required = HomeAssistantCatalogClient._decode_message(
                await socket.recv()
            )
            if not isinstance(auth_required, dict) or auth_required.get(
                "type"
            ) != "auth_required":
                raise DependencyError(
                    "ha_invalid_response",
                    "Home Assistant WebSocket 未请求认证。",
                )
            await socket.send(
                json.dumps(
                    {"type": "auth", "access_token": token},
                    separators=(",", ":"),
                )
            )
            auth_result = HomeAssistantCatalogClient._decode_message(
                await socket.recv()
            )
            if not isinstance(auth_result, dict):
                raise DependencyError(
                    "ha_invalid_response",
                    "Home Assistant WebSocket 认证响应无效。",
                )
            if auth_result.get("type") == "auth_invalid":
                raise DependencyError(
                    "ha_unauthorized",
                    "Home Assistant Token 无效。",
                )
            if auth_result.get("type") != "auth_ok":
                raise DependencyError(
                    "ha_invalid_response",
                    "Home Assistant WebSocket 认证响应无效。",
                )
            yield socket

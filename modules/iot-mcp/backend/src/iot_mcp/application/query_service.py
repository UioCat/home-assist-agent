"""Read-only application service backed by live provider state."""

from __future__ import annotations

from iot_mcp.adapters.outbound.persistence.repositories import (
    DeviceRepository,
    OperationRepository,
    StateRepository,
    ThingModelRepository,
)
from iot_mcp.application.policy import SafeControlError
from iot_mcp.domain.models import ControlOperation, DeviceInstance, ThingProduct
from iot_mcp.ports.device_provider import DeviceProvider, DeviceState


class QueryService:
    def __init__(
        self,
        *,
        models: ThingModelRepository,
        devices: DeviceRepository,
        states: StateRepository,
        operations: OperationRepository,
        providers: dict[str, DeviceProvider],
        provider_status: dict[str, str],
    ) -> None:
        self._models = models
        self._devices = devices
        self._states = states
        self._operations = operations
        self._providers = providers
        self._provider_status = provider_status

    async def list_models(self) -> list[ThingProduct]:
        return await self._models.list_products()

    async def list_devices(self) -> list[DeviceInstance]:
        return [
            device
            for device in await self._devices.list_devices()
            if device.status != "missing"
        ]

    async def list_device_cards(self) -> list[dict[str, object]]:
        cards: list[dict[str, object]] = []
        for device in await self.list_devices():
            snapshots = await self._states.latest_snapshots(device.device_id)
            values = {snapshot.identifier: snapshot.value for snapshot in snapshots}
            observed_at = max(
                (snapshot.observed_at for snapshot in snapshots),
                default=None,
            )
            freshness = (
                "unknown"
                if not snapshots
                else (
                    "stale"
                    if any(snapshot.freshness.value == "stale" for snapshot in snapshots)
                    else "fresh"
                )
            )
            binding = await self._devices.get_primary_binding(
                device.device_id, device.provider_id
            )
            route_data = binding.route_data if binding else {}
            availability = _availability_from_status(device.status)
            feature_bindings = await self._devices.list_feature_bindings(
                device.device_id
            )
            feature_risk = {
                item.identifier: (item.risk_level or device.risk_level).value
                for item in feature_bindings
            }
            model = (
                await self._models.get_model_version(device.model_version_id)
                if device.model_version_id
                else None
            )
            tsl = model.tsl_json if model else {}
            properties = list(tsl.get("properties") or [])
            services = list(tsl.get("services") or [])
            property_by_identifier = {
                str(item.get("identifier")): item
                for item in properties
                if isinstance(item, dict) and item.get("identifier")
            }
            cards.append(
                {
                    "device_id": device.device_id,
                    "display_name": device.display_name,
                    "area": device.area,
                    "device_type": str(route_data.get("device_type") or "other"),
                    "device_type_label": str(
                        route_data.get("device_type_label") or "其他"
                    ),
                    "availability": availability,
                    "provider_id": device.provider_id,
                    "provider_type": binding.provider_type if binding else device.provider_id,
                    "device_status": device.status,
                    "provider_status": self._provider_status.get(
                        device.provider_id, "unknown"
                    ),
                    "risk_level": device.risk_level.value,
                    "observed_at": observed_at,
                    "freshness": freshness,
                    "values": values,
                    "primary_control": (
                        _primary_control(
                            property_by_identifier,
                            values,
                            feature_risk,
                            device.risk_level.value,
                        )
                        if availability == "online"
                        else None
                    ),
                    "secondary_status": _secondary_status(
                        property_by_identifier, values
                    ),
                    "capability_count": len(properties) + len(services),
                }
            )
        return cards

    async def get_device(self, device_id: str) -> DeviceInstance:
        device = await self._devices.get_device(device_id)
        if device is None or device.status == "missing":
            raise SafeControlError("target_not_found", "device was not found", status_code=404)
        return device

    async def read_state(
        self, device_id: str, *, message_id: str | None = None
    ) -> DeviceState:
        device = await self.get_device(device_id)
        binding = await self._devices.get_primary_binding(device_id, device.provider_id)
        provider = self._providers.get(device.provider_id)
        if binding is None or provider is None:
            raise SafeControlError(
                "provider_unavailable",
                "live provider state is unavailable",
                status_code=503,
                retryable=True,
            )
        return await provider.read_state(
            binding.external_device_ref, message_id=message_id
        )

    async def get_operation(self, operation_id: str) -> ControlOperation:
        operation = await self._operations.get_operation(operation_id)
        if operation is None:
            raise SafeControlError(
                "operation_not_found", "operation was not found", status_code=404
            )
        return operation


def _availability_from_status(status: str) -> str:
    return {
        "active": "online",
        "offline": "offline",
        "unknown": "unknown",
    }.get(status, "unknown")


def _primary_control(
    properties: dict[str, dict[str, object]],
    values: dict[str, object],
    feature_risk: dict[str, str],
    device_risk: str,
) -> dict[str, object] | None:
    for capability in ("PowerSwitch", "LockState"):
        identifier = next(
            (
                candidate
                for candidate in sorted(properties)
                if candidate == capability or candidate.startswith(f"{capability}_")
            ),
            "",
        )
        property_definition = properties.get(identifier)
        if (
            property_definition is None
            or property_definition.get("accessMode") != "rw"
            or identifier not in values
        ):
            continue
        return {
            "kind": "property",
            "identifier": identifier,
            "name": str(property_definition.get("name") or identifier),
            "data_type": property_definition.get("dataType") or {},
            "current_value": values[identifier],
            "risk_level": feature_risk.get(identifier, device_risk),
        }
    return None


def _secondary_status(
    properties: dict[str, dict[str, object]], values: dict[str, object]
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for identifier in (
        "Brightness",
        "CurrentTemperature",
        "TargetTemperature",
        "BatteryLevel",
    ):
        property_definition = properties.get(identifier)
        if property_definition is None or identifier not in values:
            continue
        data_type = property_definition.get("dataType") or {}
        specs = data_type.get("specs") if isinstance(data_type, dict) else {}
        unit = specs.get("unit") if isinstance(specs, dict) else None
        result.append(
            {
                "identifier": identifier,
                "name": str(property_definition.get("name") or identifier),
                "value": values[identifier],
                "unit": unit,
            }
        )
        if len(result) == 2:
            break
    return result

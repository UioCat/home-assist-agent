"""Persist a provider inventory as query-oriented local projections."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from iot_mcp.adapters.outbound.persistence.repositories import (
    DeviceRepository,
    StateRepository,
    ThingModelRepository,
)
from iot_mcp.audit import AuditRecorder
from iot_mcp.domain.enums import Freshness, ModelStatus, RiskLevel
from iot_mcp.domain.models import (
    DeviceInstance,
    FeatureBinding,
    PropertySnapshot,
    ProviderDeviceBinding,
    ThingModelVersion,
    ThingProduct,
    utc_now,
)
from iot_mcp.ports.device_provider import DeviceProvider, ProviderDevice


class SyncResult:
    def __init__(
        self,
        *,
        discovered: int,
        upserted: int,
        missing: int,
        snapshots: int,
    ) -> None:
        self.discovered = discovered
        self.upserted = upserted
        self.missing = missing
        self.snapshots = snapshots


class DeviceSyncService:
    def __init__(
        self,
        provider: DeviceProvider,
        models: ThingModelRepository,
        devices: DeviceRepository,
        states: StateRepository,
        audit: AuditRecorder,
    ) -> None:
        self._provider = provider
        self._models = models
        self._devices = devices
        self._states = states
        self._audit = audit

    async def sync(self, *, message_id: str, trigger: str) -> SyncResult:
        await self._audit.record(
            message_id=message_id,
            event_type="system.request",
            service="device_sync",
            payload={
                "operation": "sync",
                "trigger": trigger,
                "provider_id": self._provider.provider_id,
                "provider_type": self._provider.provider_type,
            },
        )
        try:
            result = await self._sync(message_id=message_id)
        except Exception as error:
            await self._audit.record(
                message_id=message_id,
                event_type="system.response",
                service="device_sync",
                payload={
                    "operation": "sync",
                    "trigger": trigger,
                    "provider_id": self._provider.provider_id,
                    "error": str(error),
                },
                status="error",
                error_code=getattr(error, "category", "sync_failed"),
            )
            raise
        await self._audit.record(
            message_id=message_id,
            event_type="system.response",
            service="device_sync",
            payload={
                "operation": "sync",
                "trigger": trigger,
                "provider_id": self._provider.provider_id,
                "result": {
                    "discovered": result.discovered,
                    "upserted": result.upserted,
                    "missing": result.missing,
                    "snapshots": result.snapshots,
                },
            },
        )
        return result

    async def _sync(self, *, message_id: str) -> SyncResult:
        inventory = await self._provider.discover(message_id=message_id)
        seen_refs = {device.external_ref for device in inventory.devices}
        upserted = snapshots = 0
        for discovered in inventory.devices:
            product, model = await self._ensure_model(discovered)
            device_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{inventory.provider_type}:{discovered.external_ref}",
                )
            )
            existing = await self._devices.get_device(device_id)
            instance = await self._devices.upsert_device(
                DeviceInstance(
                    device_id=device_id,
                    product_id=product.product_id,
                    model_version_id=model.model_version_id,
                    provider_id=inventory.provider_id,
                    display_name=discovered.display_name,
                    area=discovered.area,
                    risk_level=RiskLevel(discovered.risk_level),
                    status=_device_status(discovered.state.availability),
                    created_at=(
                        existing.created_at if existing is not None else utc_now()
                    ),
                )
            )
            await self._devices.upsert_binding(
                ProviderDeviceBinding(
                    device_id=instance.device_id,
                    provider_id=inventory.provider_id,
                    provider_type=inventory.provider_type,
                    external_device_ref=discovered.external_ref,
                    route_data=discovered.metadata,
                )
            )
            feature_bindings = [
                FeatureBinding(
                    device_id=instance.device_id,
                    model_version_id=model.model_version_id,
                    **binding,
                )
                for binding in discovered.feature_bindings
            ]
            await self._devices.replace_feature_bindings(
                instance.device_id, feature_bindings
            )
            for identifier, value in discovered.state.values.items():
                await self._states.add_snapshot(
                    PropertySnapshot(
                        device_id=instance.device_id,
                        identifier=identifier,
                        value=value,
                        observed_at=discovered.state.observed_at,
                        source=inventory.provider_type,
                        freshness=Freshness(discovered.state.freshness),
                    )
                )
                snapshots += 1
            upserted += 1

        missing = 0
        for device in await self._devices.list_devices():
            if device.provider_id != inventory.provider_id:
                continue
            bindings = await self._devices.list_bindings(device.device_id)
            if any(
                binding.provider_type == inventory.provider_type
                and binding.external_device_ref not in seen_refs
                for binding in bindings
            ):
                await self._devices.upsert_device(
                    device.model_copy(
                        update={
                            "status": "missing",
                            "updated_at": utc_now(),
                        }
                    )
                )
                missing += 1
        return SyncResult(
            discovered=len(inventory.devices),
            upserted=upserted,
            missing=missing,
            snapshots=snapshots,
        )

    async def _ensure_model(
        self, device: ProviderDevice
    ) -> tuple[ThingProduct, ThingModelVersion]:
        existing = await self._models.get_product_by_key(device.product_key)
        if existing is not None and existing.source != self._provider.provider_type:
            raise ValueError(
                "system product key collides with a non-provider product"
            )
        product = await self._models.upsert_product(
            ThingProduct(
                product_id=(
                    existing.product_id
                    if existing
                    else str(uuid5(NAMESPACE_URL, device.product_key))
                ),
                product_key=device.product_key,
                name=device.product_name,
                source=self._provider.provider_type,
                capability_fingerprint=device.capability_fingerprint,
            )
        )
        versions = await self._models.list_model_versions(
            product.product_id
        )
        tsl = generated_tsl(device)
        matching = next(
            (
                version
                for version in versions
                if version.tsl_json == tsl
                and version.status is ModelStatus.ACTIVE
            ),
            None,
        )
        if matching is not None:
            return product, matching
        model = ThingModelVersion(
            product_id=product.product_id,
            version=(
                max((version.version for version in versions), default=0) + 1
            ),
            status=ModelStatus.ACTIVE,
            tsl_json=tsl,
        )
        return product, await self._models.add_active_model_version(model)


def generated_tsl(device: ProviderDevice) -> dict[str, object]:
    properties: list[dict[str, object]] = []
    services: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    for binding in sorted(
        device.feature_bindings,
        key=lambda item: (
            str(item["feature_type"]),
            str(item["identifier"]),
        ),
    ):
        identifier = str(binding["identifier"])
        if binding["feature_type"] == "property":
            metadata = binding.get("read_binding") or {}
            properties.append(
                {
                    "identifier": identifier,
                    "name": str(metadata.get("name") or identifier),
                    "accessMode": str(
                        metadata.get("access_mode")
                        or (
                            "rw"
                            if binding.get("write_binding") is not None
                            else "r"
                        )
                    ),
                    "required": False,
                    "dataType": metadata.get("data_type")
                    or inferred_data_type(
                        identifier,
                        device.state.values.get(identifier),
                    ),
                }
            )
        elif binding["feature_type"] == "service":
            metadata = binding.get("write_binding") or {}
            services.append(
                {
                    "identifier": identifier,
                    "name": str(metadata.get("name") or identifier),
                    "callType": str(metadata.get("call_type") or "async"),
                    "inputData": list(metadata.get("input_data") or []),
                    "outputData": list(metadata.get("output_data") or []),
                }
            )
        elif binding["feature_type"] == "event":
            metadata = binding.get("read_binding") or {}
            events.append(
                {
                    "identifier": identifier,
                    "name": str(metadata.get("name") or identifier),
                    "type": str(metadata.get("event_type") or "info"),
                    "outputData": list(metadata.get("output_data") or []),
                }
            )
    return {
        "schema": "https://iotx-tsl.aliyuncs.com/schema.json",
        "profile": {"productKey": device.product_key},
        "properties": properties,
        "services": services,
        "events": events,
    }


def _device_status(availability: str) -> str:
    return {
        "online": "active",
        "offline": "offline",
        "unknown": "unknown",
    }.get(availability, "unknown")


def inferred_data_type(
    identifier: str, value: object
) -> dict[str, object]:
    capability = identifier.rsplit("_", 1)[0]
    if isinstance(value, bool):
        return {"type": "bool", "specs": {}}
    if isinstance(value, int):
        specs = (
            {"min": 0, "max": 100}
            if capability == "Brightness"
            else {}
        )
        return {"type": "int", "specs": specs}
    if isinstance(value, float):
        return {"type": "double", "specs": {}}
    if capability == "LockState":
        return {
            "type": "enum",
            "specs": {"LOCK": "Locked", "UNLOCK": "Unlocked"},
        }
    return {"type": "text", "specs": {"length": 4096}}

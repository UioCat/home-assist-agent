"""Persist a provider inventory as query-oriented local projections."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from iot_mcp.adapters.outbound.persistence.repositories import (
    DeviceRepository,
    StateRepository,
    ThingModelRepository,
)
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
    def __init__(self, *, discovered: int, upserted: int, missing: int, snapshots: int) -> None:
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
    ) -> None:
        self._provider = provider
        self._models = models
        self._devices = devices
        self._states = states

    async def sync(self) -> SyncResult:
        inventory = await self._provider.discover()
        seen_refs = {device.external_ref for device in inventory.devices}
        upserted = snapshots = 0
        for discovered in inventory.devices:
            product, model = await self._ensure_model(discovered)
            device_id = str(
                uuid5(NAMESPACE_URL, f"{inventory.provider_type}:{discovered.external_ref}")
            )
            instance = await self._devices.upsert_device(
                DeviceInstance(
                    device_id=device_id,
                    product_id=product.product_id,
                    provider_id=inventory.provider_id,
                    display_name=discovered.display_name,
                    area=discovered.area,
                    risk_level=RiskLevel(discovered.risk_level),
                    status="active",
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
            for binding in discovered.feature_bindings:
                await self._devices.upsert_feature_binding(
                    FeatureBinding(
                        device_id=instance.device_id,
                        model_version_id=model.model_version_id,
                        **binding,
                    )
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
                    device.model_copy(update={"status": "missing", "updated_at": utc_now()})
                )
                missing += 1
        return SyncResult(
            discovered=len(inventory.devices),
            upserted=upserted,
            missing=missing,
            snapshots=snapshots,
        )

    async def _ensure_model(self, device: ProviderDevice) -> tuple[ThingProduct, ThingModelVersion]:
        existing = await self._models.get_product_by_key(device.product_key)
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
        versions = await self._models.list_model_versions(product.product_id)
        tsl = _generated_tsl(device)
        matching = next((version for version in versions if version.tsl_json == tsl), None)
        if matching:
            return product, matching
        model = await self._models.add_model_version(
            ThingModelVersion(
                product_id=product.product_id,
                version=(max((version.version for version in versions), default=0) + 1),
                status=ModelStatus.ACTIVE,
                tsl_json=tsl,
            )
        )
        return product, model


def _generated_tsl(device: ProviderDevice) -> dict[str, object]:
    bindings = {
        binding["identifier"]: binding for binding in device.feature_bindings
    }
    identifiers = sorted(set(bindings) | set(device.state.values))
    return {
        "schema": "https://iotx-tsl.aliyuncs.com/schema.json",
        "profile": {"productKey": device.product_key},
        "properties": [
            {
                "identifier": identifier,
                "name": identifier,
                "accessMode": (
                    "rw"
                    if bindings.get(identifier, {}).get("write_binding") is not None
                    or identifier in device.state.values
                    and identifier != "CurrentTemperature"
                    else "r"
                ),
                "dataType": _inferred_data_type(
                    identifier, device.state.values.get(identifier)
                ),
            }
            for identifier in identifiers
        ],
        "services": [],
        "events": [],
    }


def _inferred_data_type(identifier: str, value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {"type": "bool", "specs": {}}
    if isinstance(value, int):
        specs = {"min": 0, "max": 100} if identifier == "Brightness" else {}
        return {"type": "int", "specs": specs}
    if isinstance(value, float):
        return {"type": "double", "specs": {}}
    if identifier == "LockState":
        return {"type": "enum", "specs": {"LOCK": "Locked", "UNLOCK": "Unlocked"}}
    return {"type": "text", "specs": {"length": 4096}}

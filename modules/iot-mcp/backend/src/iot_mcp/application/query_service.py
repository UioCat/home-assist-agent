"""Read-only application service backed by live provider state."""

from __future__ import annotations

from iot_mcp.adapters.outbound.persistence.repositories import (
    DeviceRepository,
    OperationRepository,
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
        operations: OperationRepository,
        providers: dict[str, DeviceProvider],
    ) -> None:
        self._models = models
        self._devices = devices
        self._operations = operations
        self._providers = providers

    async def list_models(self) -> list[ThingProduct]:
        return await self._models.list_products()

    async def list_devices(self) -> list[DeviceInstance]:
        return await self._devices.list_devices()

    async def get_device(self, device_id: str) -> DeviceInstance:
        device = await self._devices.get_device(device_id)
        if device is None:
            raise SafeControlError("target_not_found", "device was not found", status_code=404)
        return device

    async def read_state(self, device_id: str) -> DeviceState:
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
        return await provider.read_state(binding.external_device_ref)

    async def get_operation(self, operation_id: str) -> ControlOperation:
        operation = await self._operations.get_operation(operation_id)
        if operation is None:
            raise SafeControlError(
                "operation_not_found", "operation was not found", status_code=404
            )
        return operation

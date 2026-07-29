import type { DeviceState } from "./types";

export function createDemoDeviceStates(): Record<string, DeviceState> {
  return {
    "device-lock": {
      device_ref: "lock.front_door",
      values: { LockState: "LOCK", BatteryLevel: 78 },
      observed_at: "2026-07-29T08:26:12Z",
      freshness: "fresh",
    },
    "device-climate": {
      device_ref: "climate.living_room",
      values: { PowerSwitch: true, CurrentTemperature: 24.3, TargetTemperature: 23 },
      observed_at: "2026-07-29T08:24:49Z",
      freshness: "fresh",
    },
    "device-lamp": {
      device_ref: "lan:desk-lamp",
      values: {},
      observed_at: "2026-07-29T07:41:04Z",
      freshness: "stale",
    },
  };
}

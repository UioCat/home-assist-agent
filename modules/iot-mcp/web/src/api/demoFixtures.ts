import type {
  ConfirmationItem,
  ConsoleOperation,
  Device,
  DeviceDetail,
  DeviceEvent,
  DeviceState,
  MessageChannelStatus,
  ProviderStatus,
  ThingModelVersion,
  ThingProduct,
} from "./types";

export const DEMO_TIMESTAMP = "2026-07-29T08:26:12Z";
const staleTimestamp = "2026-07-29T07:41:04Z";

const products: ThingProduct[] = [
  {
    product_id: "product-lock",
    product_key: "ha-lock",
    name: "家庭门锁",
    source: "home_assistant",
    capability_fingerprint: "sha256:84fd0c…e71a",
    created_at: "2026-07-21T09:00:00Z",
  },
  {
    product_id: "product-climate",
    product_key: "ha-climate",
    name: "温控设备",
    source: "home_assistant",
    capability_fingerprint: "sha256:a911d2…7e3f",
    created_at: "2026-07-22T09:00:00Z",
  },
];

const lockModel: ThingModelVersion = {
  model_version_id: "model-lock-v3",
  product_id: "product-lock",
  version: 3,
  status: "active",
  created_at: "2026-07-28T16:20:00Z",
  tsl_json: {
    schema: "https://iotx-tsl.oss-ap-southeast-1.aliyuncs.com/schema.json",
    profile: { productKey: "ha-lock" },
    properties: [
      {
        identifier: "LockState",
        name: "门锁状态",
        accessMode: "rw",
        required: true,
        dataType: { type: "enum", specs: { LOCK: "已锁", UNLOCK: "已解锁" } },
      },
      {
        identifier: "BatteryLevel",
        name: "电池电量",
        accessMode: "r",
        dataType: { type: "int", specs: { min: 0, max: 100, unit: "%" } },
      },
    ],
    services: [
      {
        identifier: "TemporaryUnlock",
        name: "临时解锁",
        callType: "async",
        inputData: [
          {
            identifier: "duration",
            name: "持续秒数",
            required: true,
            dataType: { type: "int", specs: { min: 5, max: 120, step: 5 } },
          },
        ],
        outputData: [],
      },
    ],
    events: [
      {
        identifier: "DoorForced",
        name: "异常开门",
        type: "alert",
        outputData: [],
      },
    ],
  },
};

const climateModel: ThingModelVersion = {
  model_version_id: "model-climate-v2",
  product_id: "product-climate",
  version: 2,
  status: "active",
  created_at: "2026-07-27T11:08:00Z",
  tsl_json: {
    schema: "https://iotx-tsl.oss-ap-southeast-1.aliyuncs.com/schema.json",
    profile: { productKey: "ha-climate" },
    properties: [
      {
        identifier: "PowerSwitch",
        name: "电源",
        accessMode: "rw",
        dataType: { type: "bool", specs: {} },
      },
      {
        identifier: "CurrentTemperature",
        name: "当前温度",
        accessMode: "r",
        dataType: { type: "float", specs: { unit: "°C" } },
      },
      {
        identifier: "TargetTemperature",
        name: "目标温度",
        accessMode: "rw",
        dataType: { type: "float", specs: { min: 16, max: 30, step: 0.5, unit: "°C" } },
      },
    ],
    services: [],
    events: [],
  },
};

const devices: Device[] = [
  {
    device_id: "device-lock",
    product_id: "product-lock",
    model_version_id: "model-lock-v3",
    provider_id: "home-assistant",
    display_name: "玄关门锁",
    area: "玄关",
    risk_level: "high",
    status: "active",
    created_at: "2026-07-21T09:05:00Z",
    updated_at: DEMO_TIMESTAMP,
  },
  {
    device_id: "device-climate",
    product_id: "product-climate",
    model_version_id: "model-climate-v2",
    provider_id: "home-assistant",
    display_name: "客厅空调",
    area: "客厅",
    risk_level: "medium",
    status: "active",
    created_at: "2026-07-22T09:05:00Z",
    updated_at: "2026-07-29T08:24:49Z",
  },
  {
    device_id: "device-lamp",
    product_id: null,
    model_version_id: null,
    provider_id: "lan-http-mock",
    display_name: "书房台灯",
    area: "书房",
    risk_level: "low",
    status: "offline",
    created_at: "2026-07-23T09:05:00Z",
    updated_at: staleTimestamp,
  },
];

export function buildDemoDeviceDetail(
  device: Device,
  states: Record<string, DeviceState>,
  models: ThingModelVersion[],
): DeviceDetail {
  const model = models.find(
    (candidate) => candidate.model_version_id === device.model_version_id,
  );
  return {
    device,
    bindings: [
      {
        binding_id: `binding-${device.device_id}`,
        device_id: device.device_id,
        provider_id: device.provider_id,
        provider_type: device.provider_id,
        external_device_ref: states[device.device_id].device_ref,
        binding_revision: 4,
        route_data: { area: device.area },
        created_at: device.created_at,
        updated_at: device.updated_at,
      },
    ],
    feature_bindings: model
      ? [...model.tsl_json.properties, ...model.tsl_json.services].map((feature, index) => ({
          feature_binding_id: `feature-${device.device_id}-${index}`,
          device_id: device.device_id,
          model_version_id: model.model_version_id,
          feature_type: "accessMode" in feature ? "property" : "service",
          identifier: feature.identifier,
          provider_selector: { entity_id: states[device.device_id].device_ref },
          read_binding: {},
          write_binding: "accessMode" in feature && feature.accessMode === "r" ? null : {},
          transformer: null,
          risk_level: feature.identifier.includes("Unlock") || feature.identifier === "LockState"
            ? "high"
            : device.risk_level,
          created_at: device.created_at,
        }))
      : [],
    model_versions: model ? [model] : [],
    bound_model: model ?? null,
  };
}

const baseOperations: ConsoleOperation[] = [
  {
    operation_id: "op-pending",
    device_id: "device-lock",
    source_category: "autonomous",
    source_label: "Scheduler",
    action_kind: "properties",
    action_summary: "写入 3 个属性：KeypadLock、LockState、pin",
    sensitive_values_redacted: true,
    target: "lock.front_door",
    provider_id: "home-assistant",
    provider_type: "home_assistant",
    binding_revision: 4,
    risk_level: "high",
    status: "pending_confirmation",
    created_at: "2026-07-29T08:23:10Z",
    updated_at: "2026-07-29T08:23:10Z",
  },
  {
    operation_id: "op-success",
    device_id: "device-climate",
    source_category: "human_interactive",
    source_label: "Web operator",
    action_kind: "properties",
    action_summary: "写入 1 个属性：TargetTemperature",
    sensitive_values_redacted: false,
    target: "climate.living_room",
    provider_id: "home-assistant",
    provider_type: "home_assistant",
    binding_revision: 2,
    risk_level: "medium",
    status: "succeeded",
    created_at: "2026-07-29T08:12:01Z",
    updated_at: "2026-07-29T08:12:03Z",
  },
  {
    operation_id: "op-failed",
    device_id: "device-lamp",
    source_category: "autonomous",
    source_label: "MCP agent",
    action_kind: "service",
    action_summary: "调用服务 TurnOn（0 个参数）",
    sensitive_values_redacted: false,
    target: "lan:desk-lamp",
    provider_id: "lan-http-mock",
    provider_type: "lan_http",
    binding_revision: 1,
    risk_level: "low",
    status: "failed",
    created_at: "2026-07-29T07:42:08Z",
    updated_at: "2026-07-29T07:42:18Z",
  },
];

const events: DeviceEvent[] = [
  {
    event_id: "event-1",
    device_id: "device-lock",
    identifier: "LockState",
    type: "property_changed",
    output_data: { from: "UNLOCK", to: "LOCK" },
    occurred_at: DEMO_TIMESTAMP,
    source: "home-assistant",
    created_at: DEMO_TIMESTAMP,
  },
  {
    event_id: "event-2",
    device_id: "device-climate",
    identifier: "CurrentTemperature",
    type: "property_changed",
    output_data: { value: 24.3 },
    occurred_at: "2026-07-29T08:24:49Z",
    source: "home-assistant",
    created_at: "2026-07-29T08:24:49Z",
  },
  {
    event_id: "event-3",
    device_id: "device-lamp",
    identifier: "provider_unreachable",
    type: "warning",
    output_data: { retryable: true },
    occurred_at: staleTimestamp,
    source: "lan-http-mock",
    created_at: staleTimestamp,
  },
];

const providers: ProviderStatus[] = [
  {
    provider_id: "home-assistant",
    provider_type: "home_assistant",
    status: "healthy",
    detail: null,
  },
  {
    provider_id: "lan-http-mock",
    provider_type: "lan_http",
    status: "degraded",
    detail: "1 个绑定超过新鲜度窗口",
  },
];

const messageChannels: MessageChannelStatus[] = [
  {
    channel_id: "signed-webhook",
    status: "configured",
    callback_path: "/api/v1/message-channels/signed-webhook/callbacks",
    allowed_actor_count: 2,
  },
];

export interface DemoCatalog {
  products: ThingProduct[];
  models: ThingModelVersion[];
  devices: Device[];
  operations: ConsoleOperation[];
  events: DeviceEvent[];
  providers: ProviderStatus[];
  messageChannels: MessageChannelStatus[];
}

export function createDemoCatalog(): DemoCatalog {
  return structuredClone({
    products,
    models: [lockModel, climateModel],
    devices,
    operations: baseOperations,
    events,
    providers,
    messageChannels,
  });
}

export function createDemoDeviceStates(): Record<string, DeviceState> {
  return {
    "device-lock": {
      device_ref: "lock.front_door",
      values: { LockState: "LOCK", BatteryLevel: 78 },
      observed_at: DEMO_TIMESTAMP,
      freshness: "fresh",
      availability: "online",
    },
    "device-climate": {
      device_ref: "climate.living_room",
      values: { PowerSwitch: true, CurrentTemperature: 24.3, TargetTemperature: 23 },
      observed_at: "2026-07-29T08:24:49Z",
      freshness: "fresh",
      availability: "online",
    },
    "device-lamp": {
      device_ref: "lan:desk-lamp",
      values: {},
      observed_at: staleTimestamp,
      freshness: "stale",
      availability: "offline",
    },
  };
}

export function createDemoConfirmations(
  operations: ConsoleOperation[],
): ConfirmationItem[] {
  return [
    {
      confirmation: {
        confirmation_id: "confirm-1",
        operation_id: "op-pending",
        action_hash: "hash-1",
        target: "lock.front_door",
        provider_id: "home-assistant",
        provider_type: "home_assistant",
        binding_revision: 4,
        expires_at: "2026-07-29T10:28:10Z",
        decision: "pending",
        created_at: "2026-07-29T08:23:10Z",
        risk_level: "high",
      },
      operation: operations[0],
    },
  ];
}

import type {
  ConfirmationItem,
  Device,
  DeviceDetail,
  DeviceEvent,
  DeviceState,
  IoTApi,
  MessageChannelStatus,
  Operation,
  ProviderStatus,
  SyncResult,
  ThingModelVersion,
  ThingProduct,
} from "./types";

const timestamp = "2026-07-29T08:26:12Z";
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
    provider_id: "home-assistant",
    display_name: "玄关门锁",
    area: "玄关",
    risk_level: "high",
    status: "active",
    created_at: "2026-07-21T09:05:00Z",
    updated_at: timestamp,
  },
  {
    device_id: "device-climate",
    product_id: "product-climate",
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
    provider_id: "lan-http-mock",
    display_name: "书房台灯",
    area: "书房",
    risk_level: "low",
    status: "missing",
    created_at: "2026-07-23T09:05:00Z",
    updated_at: staleTimestamp,
  },
];

const states: Record<string, DeviceState> = {
  "device-lock": {
    device_ref: "lock.front_door",
    values: { LockState: "LOCK", BatteryLevel: 78 },
    observed_at: timestamp,
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
    observed_at: staleTimestamp,
    freshness: "stale",
  },
};

function detailFor(device: Device): DeviceDetail {
  const model = device.product_id === "product-lock" ? lockModel : climateModel;
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
    model_versions: device.product_id ? [model] : [],
  };
}

const baseOperations: Operation[] = [
  {
    operation_id: "op-pending",
    device_id: "device-lock",
    initiator: "scheduler:nightly-check",
    interaction_mode: "autonomous",
    action: { type: "properties", values: { LockState: "UNLOCK" } },
    binding_id: "binding-device-lock",
    provider_id: "home-assistant",
    provider_type: "home_assistant",
    external_device_ref: "lock.front_door",
    binding_revision: 4,
    status: "pending_confirmation",
    idempotency_key: "nightly-0729",
    provider_request: null,
    provider_result: null,
    result: { confirmation_id: "confirm-1" },
    created_at: "2026-07-29T08:23:10Z",
    updated_at: "2026-07-29T08:23:10Z",
  },
  {
    operation_id: "op-success",
    device_id: "device-climate",
    initiator: "web_session:owner",
    interaction_mode: "human_interactive",
    action: { type: "properties", values: { TargetTemperature: 23 } },
    binding_id: "binding-device-climate",
    provider_id: "home-assistant",
    provider_type: "home_assistant",
    external_device_ref: "climate.living_room",
    binding_revision: 2,
    status: "succeeded",
    idempotency_key: "web-0729",
    provider_request: { service: "climate.set_temperature" },
    provider_result: { ok: true },
    result: { after: { TargetTemperature: 23 } },
    created_at: "2026-07-29T08:12:01Z",
    updated_at: "2026-07-29T08:12:03Z",
  },
  {
    operation_id: "op-failed",
    device_id: "device-lamp",
    initiator: "mcp:agent",
    interaction_mode: "autonomous",
    action: { type: "service", identifier: "TurnOn", inputs: {} },
    binding_id: "binding-device-lamp",
    provider_id: "lan-http-mock",
    provider_type: "lan_http",
    external_device_ref: "lan:desk-lamp",
    binding_revision: 1,
    status: "failed",
    idempotency_key: "mcp-0728",
    provider_request: { endpoint: "/power" },
    provider_result: { error_code: "provider_offline" },
    result: { message: "Provider 无响应" },
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
    occurred_at: timestamp,
    source: "home-assistant",
    created_at: timestamp,
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

export class DemoApiClient implements IoTApi {
  private operations = structuredClone(baseOperations);
  private confirmations: ConfirmationItem[] = [
    {
      confirmation: {
        confirmation_id: "confirm-1",
        operation_id: "op-pending",
        action_hash: "hash-1",
        authorized_actor: "owner",
        binding_id: "binding-device-lock",
        provider_id: "home-assistant",
        provider_type: "home_assistant",
        external_device_ref: "lock.front_door",
        binding_revision: 4,
        expires_at: "2026-07-29T10:28:10Z",
        decision: "pending",
        decided_at: null,
        created_at: "2026-07-29T08:23:10Z",
      },
      operation: this.operations[0],
    },
  ];

  async createSession(): Promise<void> {}
  async listThingModels() { return structuredClone(products); }
  async listThingModelVersions(productId: string) {
    return structuredClone(
      [lockModel, climateModel].filter((model) => model.product_id === productId),
    );
  }
  async validateThingModel(modelId: string) {
    return { valid: true, model_version_id: modelId };
  }
  async listDevices() { return structuredClone(devices); }
  async getDevice(deviceId: string) {
    const device = devices.find((item) => item.device_id === deviceId);
    if (!device) throw new Error("demo device not found");
    return structuredClone(detailFor(device));
  }
  async getDeviceState(deviceId: string) { return structuredClone(states[deviceId]); }
  async writeProperties(deviceId: string, values: Record<string, unknown>) {
    Object.assign(states[deviceId].values, values);
    return this.addHumanOperation(deviceId, { type: "properties", values });
  }
  async invokeService(deviceId: string, identifier: string, inputs: Record<string, unknown>) {
    return this.addHumanOperation(deviceId, { type: "service", identifier, inputs });
  }
  async listOperations() { return structuredClone(this.operations); }
  async listConfirmations(decision?: string) {
    return structuredClone(
      decision
        ? this.confirmations.filter((item) => item.confirmation.decision === decision)
        : this.confirmations,
    );
  }
  async decideConfirmation(
    confirmationId: string,
    decision: "approve" | "reject",
    _actionHash: string,
  ) {
    const item = this.confirmations.find(
      (candidate) => candidate.confirmation.confirmation_id === confirmationId,
    );
    if (!item?.operation) throw new Error("demo confirmation not found");
    item.confirmation.decision = decision === "approve" ? "approved" : "rejected";
    item.operation.status = decision === "approve" ? "succeeded" : "rejected";
    return structuredClone(item.operation);
  }
  async listEvents(deviceId?: string) {
    return structuredClone(deviceId ? events.filter((event) => event.device_id === deviceId) : events);
  }
  async listProviders(): Promise<ProviderStatus[]> {
    return [
      { provider_id: "home-assistant", provider_type: "home_assistant", status: "healthy", detail: null },
      { provider_id: "lan-http-mock", provider_type: "lan_http", status: "degraded", detail: "1 个绑定超过新鲜度窗口" },
    ];
  }
  async syncProvider(): Promise<SyncResult> {
    return { discovered: 3, upserted: 3, missing: 1, snapshots: 2 };
  }
  async listMessageChannels(): Promise<MessageChannelStatus[]> {
    return [
      {
        channel_id: "signed-webhook",
        status: "configured",
        callback_path: "/api/v1/message-channels/signed-webhook/callbacks",
        allowed_actor_count: 2,
      },
    ];
  }

  private addHumanOperation(deviceId: string, action: Record<string, unknown>): Operation {
    const operation: Operation = {
      ...baseOperations[1],
      operation_id: `demo-${this.operations.length + 1}`,
      device_id: deviceId,
      action,
      created_at: timestamp,
      updated_at: timestamp,
    };
    this.operations.unshift(operation);
    return structuredClone(operation);
  }
}

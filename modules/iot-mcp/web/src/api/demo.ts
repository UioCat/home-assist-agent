import type {
  ConfirmationItem,
  ConsoleOperation,
  Device,
  DeviceDetail,
  DeviceEvent,
  DeviceState,
  IoTApi,
  MessageChannelStatus,
  OperationResult,
  ProviderStatus,
  SyncResult,
  ThingModelVersion,
  ThingProduct,
} from "./types";
import { createDemoDeviceStates } from "./demoFixtures";

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

function detailFor(device: Device, states: Record<string, DeviceState>): DeviceDetail {
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

const baseOperations: ConsoleOperation[] = [
  {
    operation_id: "op-pending",
    device_id: "device-lock",
    source_category: "autonomous",
    source_label: "Scheduler",
    action_kind: "properties",
    action_summary: "写入 1 个属性：LockState",
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
  private sessionInvalidHandler: () => void = () => undefined;
  private readonly states = createDemoDeviceStates();
  private operations = structuredClone(baseOperations);
  private confirmations: ConfirmationItem[] = [
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
      operation: this.operations[0],
    },
  ];

  onSessionInvalid(handler: () => void) {
    this.sessionInvalidHandler = handler;
    return () => {
      if (this.sessionInvalidHandler === handler) this.sessionInvalidHandler = () => undefined;
    };
  }
  async bootstrapSession() {
    return { csrf_token: "demo-csrf", expires_at: "2099-01-01T00:00:00Z" };
  }
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
    return structuredClone(detailFor(device, this.states));
  }
  async getDeviceState(deviceId: string) { return structuredClone(this.states[deviceId]); }
  async writeProperties(deviceId: string, values: Record<string, unknown>) {
    Object.assign(this.states[deviceId].values, values);
    return this.addHumanOperation(deviceId, "properties", `写入 ${Object.keys(values).length} 个属性：${Object.keys(values).join("、")}`);
  }
  async invokeService(deviceId: string, identifier: string, inputs: Record<string, unknown>) {
    return this.addHumanOperation(deviceId, "service", `调用服务 ${identifier}（${Object.keys(inputs).length} 个参数）`);
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

  private addHumanOperation(
    deviceId: string,
    actionKind: "properties" | "service",
    actionSummary: string,
  ): OperationResult {
    const operation: ConsoleOperation = {
      ...baseOperations[1],
      operation_id: `demo-${this.operations.length + 1}`,
      device_id: deviceId,
      action_kind: actionKind,
      action_summary: actionSummary,
      created_at: timestamp,
      updated_at: timestamp,
    };
    this.operations.unshift(operation);
    return structuredClone(operation);
  }
}

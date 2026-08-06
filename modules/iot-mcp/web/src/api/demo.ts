import type {
  ConfirmationItem,
  ConsoleOperation,
  DeviceCard,
  IoTApi,
  OperationResult,
  SyncResult,
  ThingModelImportResult,
  TslDocument,
} from "./types";
import {
  buildDemoDeviceDetail,
  createDemoCatalog,
  createDemoConfirmations,
  createDemoDeviceStates,
  DEMO_TIMESTAMP,
} from "./demoFixtures";

export class DemoApiClient implements IoTApi {
  private sessionInvalidHandler: () => void = () => undefined;
  private readonly catalog = createDemoCatalog();
  private readonly states = createDemoDeviceStates();
  private readonly operations = this.catalog.operations;
  private readonly confirmations: ConfirmationItem[] =
    createDemoConfirmations(this.operations);

  onSessionInvalid(handler: () => void) {
    this.sessionInvalidHandler = handler;
    return () => {
      if (this.sessionInvalidHandler === handler) {
        this.sessionInvalidHandler = () => undefined;
      }
    };
  }

  async bootstrapSession() {
    return { auth_enabled: false, csrf_token: null, expires_at: null };
  }

  async createSession(): Promise<void> {}

  async listThingModels() {
    return structuredClone(this.catalog.products);
  }

  async listThingModelVersions(productId: string) {
    return structuredClone(
      this.catalog.models.filter((model) => model.product_id === productId),
    );
  }

  async importThingModel(
    name: string,
    tsl: TslDocument,
  ): Promise<ThingModelImportResult> {
    const productKey = String(tsl.profile.productKey ?? "");
    if (!productKey) throw new Error("profile.productKey is required");
    let product = this.catalog.products.find(
      (item) => item.product_key === productKey,
    );
    if (!product) {
      product = {
        product_id: `demo-product-${this.catalog.products.length + 1}`,
        product_key: productKey,
        name,
        source: "http",
        capability_fingerprint: `demo:${productKey}`,
        created_at: DEMO_TIMESTAMP,
      };
      this.catalog.products.push(product);
    }
    const versions = this.catalog.models.filter(
      (item) => item.product_id === product.product_id,
    );
    const model = {
      model_version_id: `demo-model-${this.catalog.models.length + 1}`,
      product_id: product.product_id,
      version: Math.max(0, ...versions.map((item) => item.version)) + 1,
      status: "draft",
      tsl_json: structuredClone(tsl),
      created_at: DEMO_TIMESTAMP,
    };
    this.catalog.models.push(model);
    return structuredClone({ product, model });
  }

  async validateThingModel(modelId: string) {
    return { valid: true, model_version_id: modelId };
  }

  async publishThingModel(modelId: string) {
    const model = this.catalog.models.find(
      (item) => item.model_version_id === modelId,
    );
    if (!model || model.status !== "draft") {
      throw new Error("only a draft model can be published");
    }
    for (const candidate of this.catalog.models) {
      if (
        candidate.product_id === model.product_id
        && candidate.status === "active"
      ) {
        candidate.status = "archived";
      }
    }
    model.status = "active";
    for (const device of this.catalog.devices) {
      if (device.product_id === model.product_id) {
        device.model_version_id = model.model_version_id;
      }
    }
    return structuredClone(model);
  }

  async archiveThingModel(modelId: string) {
    const model = this.catalog.models.find(
      (item) => item.model_version_id === modelId,
    );
    if (!model || model.status !== "draft") {
      throw new Error("only a draft model can be archived");
    }
    model.status = "archived";
    return structuredClone(model);
  }

  async exportThingModel(modelId: string) {
    const model = this.catalog.models.find(
      (item) => item.model_version_id === modelId,
    );
    if (!model) throw new Error("model not found");
    return structuredClone(model.tsl_json);
  }

  async listDevices() {
    return structuredClone(this.catalog.devices);
  }

  async listDeviceCards(): Promise<DeviceCard[]> {
    return structuredClone(
      this.catalog.devices.map((device) => {
        const state = this.states[device.device_id];
        const detail = buildDemoDeviceDetail(
          device,
          this.states,
          this.catalog.models,
        );
        const properties = detail.bound_model?.tsl_json.properties ?? [];
        const services = detail.bound_model?.tsl_json.services ?? [];
        const primaryProperty = ["PowerSwitch", "LockState"]
          .map((identifier) =>
            properties.find(
              (property) =>
                property.identifier === identifier
                && property.accessMode === "rw"
                && identifier in state.values,
            ),
          )
          .find(Boolean);
        const provider = this.catalog.providers.find(
          (item) => item.provider_id === device.provider_id,
        );
        const secondaryStatus = [
          "Brightness",
          "CurrentTemperature",
          "TargetTemperature",
          "BatteryLevel",
        ].flatMap((identifier) => {
          const property = properties.find(
            (candidate) => candidate.identifier === identifier,
          );
          if (!property || !(identifier in state.values)) return [];
          const unit = Array.isArray(property.dataType.specs)
            ? null
            : typeof property.dataType.specs.unit === "string"
              ? property.dataType.specs.unit
              : null;
          return [{
            identifier,
            name: property.name,
            value: state.values[identifier],
            unit,
          }];
        }).slice(0, 2);
        return {
          device_id: device.device_id,
          display_name: device.display_name,
          area: device.area,
          device_type: (
            device.device_id === "device-lock"
              ? "lock"
              : device.device_id === "device-climate"
                ? "climate"
                : "light"
          ) as DeviceCard["device_type"],
          device_type_label: device.device_id === "device-lock"
            ? "门锁"
            : device.device_id === "device-climate"
              ? "温控"
              : "灯具",
          availability: state.availability,
          provider_id: device.provider_id,
          provider_type: provider?.provider_type ?? device.provider_id,
          device_status: device.status,
          provider_status: provider?.status ?? "unknown",
          risk_level: device.risk_level,
          observed_at: state.observed_at,
          freshness: state.freshness,
          values: state.values,
          primary_control: primaryProperty && state.availability === "online"
            ? {
                kind: "property" as const,
                identifier: primaryProperty.identifier,
                name: primaryProperty.name,
                data_type: primaryProperty.dataType,
                current_value: state.values[primaryProperty.identifier],
                risk_level:
                  detail.feature_bindings.find(
                    (binding) => binding.identifier === primaryProperty.identifier,
                  )?.risk_level ?? device.risk_level,
              }
            : null,
          secondary_status: secondaryStatus,
          capability_count: properties.length + services.length,
        };
      }),
    );
  }

  async getDevice(deviceId: string) {
    const device = this.catalog.devices.find((item) => item.device_id === deviceId);
    if (!device) throw new Error("demo device not found");
    return structuredClone(
      buildDemoDeviceDetail(device, this.states, this.catalog.models),
    );
  }

  async getDeviceState(deviceId: string) {
    return structuredClone(this.states[deviceId]);
  }

  async writeProperties(deviceId: string, values: Record<string, unknown>) {
    Object.assign(this.states[deviceId].values, values);
    const identifiers = Object.keys(values);
    return this.addHumanOperation(
      deviceId,
      "properties",
      `写入 ${identifiers.length} 个属性：${identifiers.join("、")}`,
    );
  }

  async invokeService(
    deviceId: string,
    identifier: string,
    inputs: Record<string, unknown>,
  ) {
    const parameters = Object.keys(inputs);
    const suffix = parameters.length ? `：${parameters.join("、")}` : "";
    return this.addHumanOperation(
      deviceId,
      "service",
      `调用服务 ${identifier}（${parameters.length} 个参数${suffix}）`,
    );
  }

  async listOperations() {
    return structuredClone(this.operations);
  }

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
    return structuredClone(
      deviceId
        ? this.catalog.events.filter((event) => event.device_id === deviceId)
        : this.catalog.events,
    );
  }

  async listProviders() {
    return structuredClone(this.catalog.providers);
  }

  async syncProvider(): Promise<SyncResult> {
    return { discovered: 3, upserted: 3, missing: 1, snapshots: 2 };
  }

  async listMessageChannels() {
    return structuredClone(this.catalog.messageChannels);
  }

  private addHumanOperation(
    deviceId: string,
    actionKind: "properties" | "service",
    actionSummary: string,
  ): OperationResult {
    const operation: ConsoleOperation = {
      ...this.catalog.operations[1],
      operation_id: `demo-${this.operations.length + 1}`,
      device_id: deviceId,
      action_kind: actionKind,
      action_summary: actionSummary,
      sensitive_values_redacted: false,
      created_at: DEMO_TIMESTAMP,
      updated_at: DEMO_TIMESTAMP,
    };
    this.operations.unshift(operation);
    return structuredClone(operation);
  }
}

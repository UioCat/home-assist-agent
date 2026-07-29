import type {
  ConfirmationItem,
  ConsoleOperation,
  IoTApi,
  OperationResult,
  SyncResult,
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
    return { csrf_token: "demo-csrf", expires_at: "2099-01-01T00:00:00Z" };
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

  async validateThingModel(modelId: string) {
    return { valid: true, model_version_id: modelId };
  }

  async listDevices() {
    return structuredClone(this.catalog.devices);
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

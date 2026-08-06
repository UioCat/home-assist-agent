import type {
  ConfirmationItem,
  Device,
  DeviceCard,
  DeviceDetail,
  DeviceEvent,
  DeviceState,
  IoTApi,
  MessageChannelStatus,
  ConsoleOperation,
  OperationResult,
  ProviderStatus,
  SyncResult,
  ThingModelImportResult,
  ThingModelVersion,
  ThingProduct,
  TslDocument,
  SessionInfo,
} from "./types";

interface ErrorBody {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
    request_id?: string;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly requestId?: string;

  constructor(options: {
    status: number;
    code: string;
    message: string;
    retryable?: boolean;
    requestId?: string;
  }) {
    super(options.message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.retryable = options.retryable ?? false;
    this.requestId = options.requestId;
  }
}

export class HttpApiClient implements IoTApi {
  private csrfToken: string | null = null;
  private sessionInvalidHandler: () => void;

  constructor(
    private readonly baseUrl = "/api/v1",
    onSessionInvalid: () => void = () => undefined,
  ) {
    this.sessionInvalidHandler = onSessionInvalid;
  }

  onSessionInvalid(handler: () => void): () => void {
    this.sessionInvalidHandler = handler;
    return () => {
      if (this.sessionInvalidHandler === handler) {
        this.sessionInvalidHandler = () => undefined;
      }
    };
  }

  async bootstrapSession(): Promise<SessionInfo> {
    const session = await this.request<SessionInfo>("/auth/session");
    this.csrfToken = session.csrf_token;
    return session;
  }

  async createSession(adminToken: string): Promise<void> {
    const response = await this.request<SessionInfo>("/auth/session", {
      method: "POST",
      adminToken,
    });
    this.csrfToken = response.csrf_token;
  }

  listThingModels() {
    return this.request<ThingProduct[]>("/thing-models");
  }

  listThingModelVersions(productId: string) {
    return this.request<ThingModelVersion[]>(
      `/thing-models/${encodeURIComponent(productId)}/versions`,
    );
  }

  importThingModel(name: string, tsl: TslDocument) {
    return this.request<ThingModelImportResult>("/thing-models", {
      method: "POST",
      body: { name, tsl },
    });
  }

  validateThingModel(modelId: string) {
    return this.request<{ valid: boolean; model_version_id: string }>(
      `/thing-models/${encodeURIComponent(modelId)}:validate`,
      { method: "POST" },
    );
  }

  publishThingModel(modelId: string) {
    return this.request<ThingModelVersion>(
      `/thing-models/${encodeURIComponent(modelId)}:publish`,
      { method: "POST" },
    );
  }

  archiveThingModel(modelId: string) {
    return this.request<ThingModelVersion>(
      `/thing-models/${encodeURIComponent(modelId)}:archive`,
      { method: "POST" },
    );
  }

  exportThingModel(modelId: string) {
    return this.request<TslDocument>(
      `/thing-models/${encodeURIComponent(modelId)}:export`,
    );
  }

  listDevices() {
    return this.request<Device[]>("/devices");
  }

  listDeviceCards() {
    return this.request<DeviceCard[]>("/device-cards");
  }

  getDevice(deviceId: string) {
    return this.request<DeviceDetail>(`/devices/${encodeURIComponent(deviceId)}`);
  }

  getDeviceState(deviceId: string) {
    return this.request<DeviceState>(`/devices/${encodeURIComponent(deviceId)}/state`);
  }

  writeProperties(deviceId: string, values: Record<string, unknown>) {
    return this.request<OperationResult>(
      `/devices/${encodeURIComponent(deviceId)}/properties:write`,
      { method: "POST", body: { values }, idempotencyKey: crypto.randomUUID() },
    );
  }

  invokeService(deviceId: string, identifier: string, inputs: Record<string, unknown>) {
    return this.request<OperationResult>(
      `/devices/${encodeURIComponent(deviceId)}/services/${encodeURIComponent(identifier)}:invoke`,
      { method: "POST", body: { inputs }, idempotencyKey: crypto.randomUUID() },
    );
  }

  listOperations() {
    return this.request<ConsoleOperation[]>("/operations");
  }

  listConfirmations(decision?: string) {
    const query = decision ? `?decision=${encodeURIComponent(decision)}` : "";
    return this.request<ConfirmationItem[]>(`/confirmations${query}`);
  }

  decideConfirmation(
    confirmationId: string,
    decision: "approve" | "reject",
    actionHash: string,
  ) {
    return this.request<OperationResult>(
      `/confirmations/${encodeURIComponent(confirmationId)}:${decision}`,
      { method: "POST", body: { action_hash: actionHash } },
    );
  }

  listEvents(deviceId?: string) {
    const query = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : "";
    return this.request<DeviceEvent[]>(`/device-events${query}`);
  }

  listProviders() {
    return this.request<ProviderStatus[]>("/providers");
  }

  syncProvider(providerId: string) {
    return this.request<SyncResult>(`/providers/${encodeURIComponent(providerId)}:sync`, {
      method: "POST",
    });
  }

  listMessageChannels() {
    return this.request<MessageChannelStatus[]>("/message-channels");
  }

  private async request<T>(
    path: string,
    options: {
      method?: "GET" | "POST";
      body?: unknown;
      adminToken?: string;
      idempotencyKey?: string;
    } = {},
  ): Promise<T> {
    const method = options.method ?? "GET";
    const headers: Record<string, string> = { Accept: "application/json" };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (options.adminToken) headers.Authorization = `Bearer ${options.adminToken}`;
    if (method !== "GET" && !options.adminToken && this.csrfToken) {
      headers["X-CSRF-Token"] = this.csrfToken;
    }
    if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        credentials: "include",
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch {
      throw new ApiError({
        status: 0,
        code: "network_error",
        message: "无法连接 IoT MCP API。",
        retryable: true,
      });
    }
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as ErrorBody;
      if (response.status === 401) {
        this.csrfToken = null;
        this.sessionInvalidHandler();
      }
      throw new ApiError({
        status: response.status,
        code: body.error?.code ?? "http_error",
        message: body.error?.message ?? `API 请求失败（${response.status}）`,
        retryable: body.error?.retryable,
        requestId: body.error?.request_id ?? response.headers.get("X-Request-ID") ?? undefined,
      });
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
}

export type RiskLevel = "low" | "medium" | "high";
export type Freshness = "fresh" | "stale" | "unknown";

export interface ThingProduct {
  product_id: string;
  product_key: string;
  name: string;
  source: string;
  capability_fingerprint: string;
  created_at: string;
}

export interface TslDataType {
  type: "int" | "float" | "double" | "text" | "date" | "bool" | "enum" | "struct" | "array";
  specs: Record<string, unknown> | Array<Record<string, unknown>>;
}

export interface TslParameter {
  identifier: string;
  name: string;
  required?: boolean;
  dataType: TslDataType;
}

export interface TslProperty extends TslParameter {
  accessMode: "r" | "rw";
}

export interface TslService {
  identifier: string;
  name: string;
  required?: boolean;
  callType?: string;
  inputData: TslParameter[];
  outputData: TslParameter[];
}

export interface TslEventDefinition {
  identifier: string;
  name: string;
  type: string;
  outputData: TslParameter[];
}

export interface TslDocument {
  schema: string;
  profile: Record<string, unknown>;
  properties: TslProperty[];
  services: TslService[];
  events: TslEventDefinition[];
}

export interface ThingModelVersion {
  model_version_id: string;
  product_id: string;
  version: number;
  status: string;
  tsl_json: TslDocument;
  created_at: string;
}

export interface Device {
  device_id: string;
  product_id: string | null;
  provider_id: string;
  display_name: string;
  area: string | null;
  risk_level: RiskLevel;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ProviderBinding {
  binding_id: string;
  device_id: string;
  provider_id: string | null;
  provider_type: string;
  external_device_ref: string;
  binding_revision: number;
  route_data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface FeatureBinding {
  feature_binding_id: string;
  device_id: string;
  model_version_id: string | null;
  feature_type: string;
  identifier: string;
  provider_selector: Record<string, unknown>;
  read_binding: Record<string, unknown> | null;
  write_binding: Record<string, unknown> | null;
  transformer: Record<string, unknown> | null;
  risk_level: RiskLevel | null;
  created_at: string;
}

export interface DeviceDetail {
  device: Device;
  bindings: ProviderBinding[];
  feature_bindings: FeatureBinding[];
  model_versions: ThingModelVersion[];
}

export interface DeviceState {
  device_ref: string;
  values: Record<string, unknown>;
  observed_at: string;
  freshness: Freshness;
}

export interface OperationResult {
  operation_id: string;
  device_id: string;
  status: string;
}

export interface ConsoleOperation extends OperationResult {
  source_category: "human_interactive" | "autonomous";
  source_label: string;
  action_kind: "properties" | "service" | "unknown";
  action_summary: string;
  sensitive_values_redacted: boolean;
  target: string;
  provider_id: string | null;
  provider_type: string | null;
  binding_revision: number | null;
  risk_level: RiskLevel | "unknown";
  created_at: string;
  updated_at: string;
}

export interface ConsoleConfirmation {
  confirmation_id: string;
  operation_id: string;
  action_hash: string;
  target: string;
  provider_id: string | null;
  provider_type: string | null;
  binding_revision: number;
  expires_at: string;
  decision: string;
  created_at: string;
  risk_level: "high";
}

export interface ConfirmationItem {
  confirmation: ConsoleConfirmation;
  operation: ConsoleOperation | null;
}

export interface SessionInfo {
  csrf_token: string;
  expires_at: string;
}

export interface DeviceEvent {
  event_id: string;
  device_id: string;
  identifier: string;
  type: string;
  output_data: Record<string, unknown>;
  occurred_at: string;
  source: string;
  created_at: string;
}

export interface ProviderStatus {
  provider_id: string;
  provider_type: string;
  status: string;
  detail: string | null;
}

export interface MessageChannelStatus {
  channel_id: string;
  status: string;
  callback_path: string;
  allowed_actor_count: number;
}

export interface SyncResult {
  discovered: number;
  upserted: number;
  missing: number;
  snapshots: number;
}

export interface IoTApi {
  onSessionInvalid(handler: () => void): () => void;
  bootstrapSession(): Promise<SessionInfo>;
  createSession(adminToken: string): Promise<void>;
  listThingModels(): Promise<ThingProduct[]>;
  listThingModelVersions(productId: string): Promise<ThingModelVersion[]>;
  validateThingModel(modelId: string): Promise<{ valid: boolean; model_version_id: string }>;
  listDevices(): Promise<Device[]>;
  getDevice(deviceId: string): Promise<DeviceDetail>;
  getDeviceState(deviceId: string): Promise<DeviceState>;
  writeProperties(deviceId: string, values: Record<string, unknown>): Promise<OperationResult>;
  invokeService(
    deviceId: string,
    identifier: string,
    inputs: Record<string, unknown>,
  ): Promise<OperationResult>;
  listOperations(): Promise<ConsoleOperation[]>;
  listConfirmations(decision?: string): Promise<ConfirmationItem[]>;
  decideConfirmation(
    confirmationId: string,
    decision: "approve" | "reject",
    actionHash: string,
  ): Promise<OperationResult>;
  listEvents(deviceId?: string): Promise<DeviceEvent[]>;
  listProviders(): Promise<ProviderStatus[]>;
  syncProvider(providerId: string): Promise<SyncResult>;
  listMessageChannels(): Promise<MessageChannelStatus[]>;
}

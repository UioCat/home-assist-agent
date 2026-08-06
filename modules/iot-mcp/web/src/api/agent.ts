export interface AgentHealth {
  backend: "online";
  codex: {
    installed: boolean;
    authenticated: boolean;
    error_code: string | null;
  };
  ha_mcp: {
    configured: boolean;
    connected: boolean;
    tool_count: number;
    error_code: string | null;
  };
}

export interface AgentTraceStep {
  stage: string;
  status: string;
  summary: string;
}

export interface AgentToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result: string;
}

export interface AgentCommandResult {
  message_id: string;
  request_id: string;
  conversation_id: string | null;
  category: string;
  route: string;
  status: string;
  message: string;
  tool_call: AgentToolCall | null;
  tool_calls: AgentToolCall[];
  warnings: string[];
  trace: AgentTraceStep[];
  elapsed_ms: number;
  error_code: string | null;
}

export interface AgentConversationMessage {
  message_id: string;
  request_id: string;
  channel: string;
  command: string;
  status: string;
  response: AgentCommandResult | null;
  created_at: string;
  completed_at: string | null;
}

export interface AgentConversation {
  conversation_id: string;
  status: string;
  messages: AgentConversationMessage[];
}

export interface AgentConversationCreated {
  message_id: string;
  request_id: string;
  conversation_id: string;
  status: string;
}

export interface AgentAuditMessage {
  message_id: string;
  request_id: string;
  conversation_id: string | null;
  command: string | null;
  response: string | null;
  input_type: "message" | "event";
  correlation_id: string | null;
  status: string;
  event_count: number;
  started_at: string;
  ended_at: string;
}

export interface AgentAuditEvent {
  event_id: string;
  message_id: string;
  request_id: string;
  conversation_id: string | null;
  sequence: number;
  event_type: string;
  service: string;
  payload: Record<string, unknown>;
  status: string;
  error_code: string | null;
  correlation_id: string | null;
  causation_id: string | null;
  created_at: string;
}

export interface AgentApi {
  getHealth(): Promise<AgentHealth>;
  getCurrentConversation(): Promise<AgentConversation>;
  createConversation(): Promise<AgentConversationCreated>;
  submitCommand(
    command: string,
    conversationId?: string,
  ): Promise<AgentCommandResult>;
  listAuditMessages(limit?: number): Promise<AgentAuditMessage[]>;
  listAuditEvents(messageId: string): Promise<AgentAuditEvent[]>;
}

export class AgentApiClient implements AgentApi {
  constructor(
    private readonly baseUrl =
      import.meta.env.VITE_AGENT_API_BASE?.trim() || "/agent-api",
  ) {}

  getHealth() {
    return this.request<AgentHealth>("/health");
  }

  getCurrentConversation() {
    return this.request<AgentConversation>("/conversations/current");
  }

  createConversation() {
    return this.request<AgentConversationCreated>("/conversations", {
      method: "POST",
      body: { message_id: crypto.randomUUID() },
    });
  }

  submitCommand(command: string, conversationId?: string) {
    return this.request<AgentCommandResult>("/commands", {
      method: "POST",
      body: {
        command,
        message_id: crypto.randomUUID(),
        ...(conversationId ? { conversation_id: conversationId } : {}),
      },
    });
  }

  listAuditMessages(limit = 50) {
    return this.request<AgentAuditMessage[]>(`/audit?limit=${limit}`);
  }

  listAuditEvents(messageId: string) {
    return this.request<AgentAuditEvent[]>(
      `/audit/${encodeURIComponent(messageId)}`,
    );
  }

  private async request<T>(
    path: string,
    options: { method?: "GET" | "POST"; body?: unknown } = {},
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload?.detail;
      throw new Error(typeof detail === "string" ? detail : "Agent API 请求失败");
    }
    return payload as T;
  }
}

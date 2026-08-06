import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DemoApiClient } from "../api/demo";
import { App } from "../app";

const health = {
  backend: "online",
  codex: { installed: true, authenticated: true, error_code: null },
  ha_mcp: {
    configured: true,
    connected: true,
    tool_count: 4,
    error_code: null,
  },
};

const commandResult = {
  message_id: "message-ui-1",
  request_id: "message-ui-1",
  conversation_id: "conversation-current",
  category: "direct_iot",
  route: "home_assistant_mcp",
  status: "success",
  message: "Home Assistant 已处理该指令。",
  tool_call: {
    name: "assist.HassTurnOn",
    arguments: { name: "客厅灯" },
    result: "Done",
  },
  tool_calls: [],
  resolution: null,
  warnings: [],
  trace: [
    { stage: "input", status: "success", summary: "收到指令" },
    { stage: "dispatch", status: "success", summary: "Home Assistant MCP" },
  ],
  elapsed_ms: 182,
  error_code: null,
};

const auditMessages = [
  {
    message_id: "message-audit-1",
    command: "客厅太暗了",
    response: "准备调亮客厅灯。",
    status: "success",
    event_count: 2,
    input_type: "message",
    correlation_id: "message-audit-1",
    started_at: "2026-08-05T10:00:00Z",
    ended_at: "2026-08-05T10:00:02Z",
  },
];

const auditEvents = [
  {
    event_id: "event-1",
    message_id: "message-audit-1",
    sequence: 1,
    event_type: "user.request",
    service: "web",
    payload: { command: "客厅太暗了" },
    status: "success",
    error_code: null,
    correlation_id: "message-audit-1",
    causation_id: null,
    created_at: "2026-08-05T10:00:00Z",
  },
];

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("unified Home Assist console", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
      "message-ui-1" as `${string}-${string}-${string}-${string}-${string}`,
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses the command center as the default page and sends a unique message id to port 8080", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/agent-api/health") return json(health);
      if (url === "/agent-api/commands") return json(commandResult);
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App api={new DemoApiClient()} demo={false} />);

    expect(
      await screen.findByRole("heading", { name: "家庭指令中心" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("智能助手可用")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "家庭指令" }), "打开客厅灯");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/agent-api/commands",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            command: "打开客厅灯",
            message_id: "message-ui-1",
          }),
        }),
      ),
    );
    expect(await screen.findByText("Home Assistant 已处理该指令。")).toBeInTheDocument();
  });

  it("shows conversation history, reuses its id, and starts a new conversation", async () => {
    const conversation = {
      conversation_id: "conversation-current",
      status: "active",
      messages: [
        {
          message_id: "message-history-1",
          request_id: "message-history-1",
          channel: "console",
          command: "打开书房灯",
          status: "completed",
          response: {
            ...commandResult,
            message_id: "message-history-1",
            request_id: "message-history-1",
            message: "书房灯已打开。",
          },
          created_at: "2026-08-06T08:00:00Z",
          completed_at: "2026-08-06T08:00:01Z",
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/agent-api/health") return json(health);
      if (url === "/agent-api/conversations/current") return json(conversation);
      if (url === "/agent-api/commands") return json(commandResult);
      if (url === "/agent-api/conversations") {
        return json({
          message_id: "message-ui-1",
          request_id: "message-ui-1",
          conversation_id: "conversation-new",
          status: "creating",
        });
      }
      throw new Error(`unexpected URL: ${url} ${String(init?.method)}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App api={new DemoApiClient()} demo={false} />);

    expect(await screen.findByText("打开书房灯")).toBeInTheDocument();
    expect(screen.getByText("书房灯已打开。")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "家庭指令" }), "再关掉");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/agent-api/commands",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            command: "再关掉",
            message_id: "message-ui-1",
            conversation_id: "conversation-current",
          }),
        }),
      ),
    );

    await user.click(screen.getByRole("button", { name: "新建会话" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/agent-api/conversations",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(screen.queryByText("打开书房灯")).not.toBeInTheDocument();
  });

  it("renders the Agent audit ledger inside the unified shell", async () => {
    window.history.replaceState({}, "", "/audit");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/agent-api/audit?limit=50") return json(auditMessages);
        if (url === "/agent-api/audit/message-audit-1") return json(auditEvents);
        throw new Error(`unexpected URL: ${url}`);
      }),
    );

    render(<App api={new DemoApiClient()} demo={false} />);

    expect(
      await screen.findByRole("heading", { name: "审计中心" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("用户 → 系统")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /审计中心/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("keeps the former IoT overview at its own route in the same navigation", async () => {
    window.history.replaceState({}, "", "/overview");

    render(<App api={new DemoApiClient()} demo={false} />);

    expect(
      await screen.findByRole("heading", { name: "先处理失联与待确认" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /家庭概览/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: /家庭指令中心/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /审计中心/ })).toBeInTheDocument();
  });

  it("keeps the Agent command center available when the independent IoT backend is offline", async () => {
    class OfflineIoTApi extends DemoApiClient {
      override async bootstrapSession(): Promise<never> {
        throw new Error("8090 offline");
      }
    }
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === "/agent-api/health") return json(health);
        throw new Error(`unexpected URL: ${String(input)}`);
      }),
    );

    render(<App api={new OfflineIoTApi()} demo={false} />);

    expect(
      await screen.findByRole("heading", { name: "家庭指令中心" }),
    ).toBeInTheDocument();
    expect(screen.getByText("设备服务未连接")).toBeInTheDocument();
    expect(screen.queryByLabelText("Admin Token")).not.toBeInTheDocument();
  });

  it("keeps implementation terminology out of the household-facing shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/agent-api/health") return json(health);
        if (url === "/agent-api/conversations/current") {
          return json({
            conversation_id: "conversation-current",
            status: "active",
            messages: [],
          });
        }
        throw new Error(`unexpected URL: ${url}`);
      }),
    );

    render(<App api={new DemoApiClient()} demo={false} />);

    expect(await screen.findByText("智能助手可用")).toBeInTheDocument();
    expect(screen.getAllByText("家庭控制台").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /设备来源/ })).toBeInTheDocument();
    expect(screen.queryByText("HOME CONVERSATION")).not.toBeInTheDocument();
    expect(screen.queryByText("CONTINUE CONVERSATION")).not.toBeInTheDocument();
    expect(screen.queryByText("仅在本机处理")).not.toBeInTheDocument();
    expect(screen.queryByText(/IoT MCP/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/OWNER/i)).not.toBeInTheDocument();
  });
});

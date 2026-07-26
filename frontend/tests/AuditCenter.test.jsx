import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../src/App";


const healthResponse = {
  backend: "online",
  codex: {
    installed: true,
    authenticated: true,
    error_code: null,
  },
  ha_mcp: {
    configured: false,
    connected: false,
    tool_count: 0,
    error_code: "ha_not_configured",
  },
};

const auditMessages = [
  {
    message_id: "message-audit-1",
    command: "客厅太暗了",
    response: "准备调暗客厅灯。",
    status: "success",
    event_count: 4,
    input_type: "message",
    correlation_id: "message-audit-1",
    started_at: "2026-07-26T05:30:00Z",
    ended_at: "2026-07-26T05:30:02Z",
  },
  {
    message_id: "message-audit-2",
    command: "打开客厅灯",
    response: "Home Assistant 已处理该指令。",
    status: "success",
    event_count: 2,
    input_type: "event",
    correlation_id: "home-session-1",
    started_at: "2026-07-26T05:20:00Z",
    ended_at: "2026-07-26T05:20:01Z",
  },
];

const auditEvents = {
  "message-audit-1": [
    {
      event_id: "event-1",
      message_id: "message-audit-1",
      sequence: 1,
      event_type: "user.request",
      service: "web",
      payload: { command: "客厅太暗了", reasoning: "medium" },
      status: "success",
      error_code: null,
      correlation_id: "message-audit-1",
      causation_id: null,
      created_at: "2026-07-26T05:30:00Z",
    },
    {
      event_id: "event-2",
      message_id: "message-audit-1",
      sequence: 2,
      event_type: "codex.request",
      service: "codex_cli",
      payload: {
        purpose: "route",
        reasoning: "low",
        prompt: "你是 Home Assist Agent 的指令路由器。",
      },
      status: "success",
      error_code: null,
      correlation_id: "message-audit-1",
      causation_id: null,
      created_at: "2026-07-26T05:30:01Z",
    },
  ],
  "message-audit-2": [
    {
      event_id: "event-3",
      message_id: "message-audit-2",
      sequence: 1,
      event_type: "event.received",
      service: "event_channel",
      payload: {
        event_id: "presence-1",
        event_type: "person.seated",
        source: "home_assistant",
        subject_id: "owner",
        location: "study",
      },
      status: "success",
      error_code: null,
      correlation_id: "home-session-1",
      causation_id: "event-entered-home",
      created_at: "2026-07-26T05:20:00Z",
    },
  ],
};

function jsonResponse(payload, ok = true) {
  return {
    ok,
    json: async () => payload,
  };
}

describe("Audit center", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/audit");
    global.fetch = vi.fn(async (url) => {
      if (url === "/api/health") {
        return jsonResponse(healthResponse);
      }
      if (url === "/api/audit?limit=50") {
        return jsonResponse(auditMessages);
      }
      const messageId = url.split("/").at(-1);
      return jsonResponse(auditEvents[messageId] || []);
    });
  });

  afterEach(() => {
    window.history.pushState({}, "", "/");
    vi.restoreAllMocks();
  });

  it("renders the message index and complete event payloads", async () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "审计中心" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "审计中心" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      (await screen.findAllByText("客厅太暗了")).length,
    ).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText("用户 → 系统")).toBeInTheDocument();
    expect(screen.getByText("系统 → Codex")).toBeInTheDocument();
    expect(screen.getAllByText("查看完整 Payload")).toHaveLength(2);
    expect(screen.getAllByText("message-audit-1").length).toBeGreaterThan(0);
  });

  it("switches the selected message and loads its trace", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(
      await screen.findByRole("button", { name: /打开客厅灯/ }),
    );

    expect(await screen.findByText("事件源 → 系统")).toBeInTheDocument();
    expect(screen.getByText("person.seated · owner · study")).toBeInTheDocument();
    expect(screen.getAllByText("home-session-1").length).toBeGreaterThan(0);
    expect(screen.getByText("event-entered-home")).toBeInTheDocument();
    expect(screen.getByText("EVENT TRACE")).toBeInTheDocument();
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/audit/message-audit-2",
      );
    });
  });
});

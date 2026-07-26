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
    configured: true,
    connected: true,
    tool_count: 4,
    error_code: null,
  },
};

const commandResponse = {
  request_id: "request-1",
  category: "direct_iot",
  route: "home_assistant_mcp",
  status: "success",
  message: "Home Assistant 已处理该指令。",
  tool_call: {
    name: "assist.HassTurnOn",
    arguments: { name: "客厅灯" },
    result: "Done",
  },
  trace: [
    { stage: "input", status: "success", summary: "收到指令" },
    { stage: "classify", status: "success", summary: "直接 IoT" },
    {
      stage: "dispatch",
      status: "success",
      summary: "Home Assistant MCP",
    },
    { stage: "result", status: "success", summary: "工具返回成功" },
  ],
  elapsed_ms: 182,
  error_code: null,
};

function jsonResponse(payload, ok = true) {
  return {
    ok,
    json: async () => payload,
  };
}

describe("Home Assist Agent", () => {
  beforeEach(() => {
    global.fetch = vi.fn(async (url) => {
      if (url === "/api/health") {
        return jsonResponse(healthResponse);
      }
      return jsonResponse(commandResponse);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the command workbench and live dependency status", async () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "家庭指令中心" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "家庭指令" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Codex 已就绪")).toBeInTheDocument();
    expect(screen.getByText("HA MCP · 4 个工具")).toBeInTheDocument();
  });

  it("submits the command with fixed routing policy and renders the execution rail", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByLabelText("固定推理策略")).toHaveTextContent(
      "意图路由LOW设备规划MEDIUM普通回答HIGH",
    );
    await user.type(
      screen.getByRole("textbox", { name: "家庭指令" }),
      "打开客厅灯",
    );
    await user.click(screen.getByRole("button", { name: "执行指令" }));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/commands",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            command: "打开客厅灯",
          }),
        }),
      );
    });
    expect(await screen.findByText("直接控制")).toBeInTheDocument();
    expect(screen.getByText("Home Assistant 已处理该指令。")).toBeInTheDocument();
    expect(screen.getByText("Home Assistant MCP")).toBeInTheDocument();
    expect(screen.getByText("assist.HassTurnOn")).toBeInTheDocument();
    expect(screen.getByText("182 ms")).toBeInTheDocument();
  });

  it("disables submission while a command is running", async () => {
    let resolveCommand;
    global.fetch = vi.fn((url) => {
      if (url === "/api/health") {
        return Promise.resolve(jsonResponse(healthResponse));
      }
      return new Promise((resolve) => {
        resolveCommand = () => resolve(jsonResponse(commandResponse));
      });
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(
      screen.getByRole("textbox", { name: "家庭指令" }),
      "打开客厅灯",
    );
    await user.click(screen.getByRole("button", { name: "执行指令" }));

    expect(screen.getByRole("button", { name: "处理中…" })).toBeDisabled();
    resolveCommand();
    expect(await screen.findByText("直接控制")).toBeInTheDocument();
  });

  it("uses example commands and presents blocked outcomes as alerts", async () => {
    global.fetch = vi.fn(async (url) => {
      if (url === "/api/health") {
        return jsonResponse(healthResponse);
      }
      return jsonResponse({
        ...commandResponse,
        category: "indirect_iot",
        status: "blocked",
        message: "该工具或目标不在 MVP 的安全执行范围内。",
        tool_call: null,
        error_code: "unsafe_target",
      });
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "客厅太暗了" }));
    expect(screen.getByRole("textbox", { name: "家庭指令" })).toHaveValue(
      "客厅太暗了",
    );
    await user.click(screen.getByRole("button", { name: "执行指令" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "该工具或目标不在 MVP 的安全执行范围内。",
    );
    expect(screen.getByText("间接控制")).toBeInTheDocument();
    expect(screen.getByText("unsafe_target")).toBeInTheDocument();
  });

  it("shows a useful error when the API cannot be reached", async () => {
    global.fetch = vi.fn((url) => {
      if (url === "/api/health") {
        return Promise.resolve(jsonResponse(healthResponse));
      }
      return Promise.reject(new Error("network down"));
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(
      screen.getByRole("textbox", { name: "家庭指令" }),
      "你好",
    );
    await user.click(screen.getByRole("button", { name: "执行指令" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法连接本地服务",
    );
  });
});

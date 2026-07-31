import { act, render, screen, waitFor } from "@testing-library/react";

import { DemoApiClient } from "../api/demo";
import { App } from "../app";

describe("application session flow", () => {
  it("restores an existing browser session during bootstrap", async () => {
    const api = new DemoApiClient();
    const bootstrap = vi.spyOn(api, "bootstrapSession");

    render(<App api={api} demo={false} />);

    expect(screen.getByRole("heading", { name: "正在恢复安全 Session" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "家庭设备概览" })).toBeInTheDocument(),
    );
    expect(bootstrap).toHaveBeenCalledOnce();
  });

  it("returns to the session gate when bootstrap reports an expired session", async () => {
    const api = new DemoApiClient();
    vi.spyOn(api, "bootstrapSession").mockRejectedValue(new Error("expired"));

    render(<App api={api} demo={false} />);

    expect(await screen.findByRole("heading", { name: "连接家庭设备控制台" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "家庭设备概览" })).not.toBeInTheDocument();
  });

  it("returns an authenticated page to the gate when the API reports session invalid", async () => {
    class ExpirableDemoApiClient extends DemoApiClient {
      private invalidHandler: () => void = () => undefined;

      override onSessionInvalid(handler: () => void) {
        this.invalidHandler = handler;
        return () => {
          if (this.invalidHandler === handler) this.invalidHandler = () => undefined;
        };
      }

      expire() {
        this.invalidHandler();
      }
    }
    const api = new ExpirableDemoApiClient();
    render(<App api={api} demo={false} />);
    await screen.findByRole("heading", { name: "家庭设备概览" });

    act(() => api.expire());

    expect(await screen.findByRole("heading", { name: "连接家庭设备控制台" })).toBeInTheDocument();
  });
});

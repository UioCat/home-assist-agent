import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiProvider } from "../api/context";
import { DemoApiClient } from "../api/demo";
import { PageState } from "../components/PageState";
import { DeviceDetailPage } from "../pages/DeviceDetailPage";
import { OperationsPage } from "../pages/OperationsPage";

function renderWithApi(
  ui: React.ReactNode,
  api = new DemoApiClient(),
  initialEntries = ["/"],
) {
  return render(
    <ApiProvider api={api}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </ApiProvider>,
  );
}

describe("console states and safety interactions", () => {
  it("renders explicit loading, error, and empty states", () => {
    const { rerender } = render(<PageState state="loading" label="正在读取设备" />);
    expect(screen.getByRole("status")).toHaveTextContent("正在读取设备");
    rerender(<PageState state="empty" label="没有设备" />);
    expect(screen.getByText("没有设备")).toBeInTheDocument();
    rerender(<PageState state="error" label="API 不可用" detail="req-1" />);
    expect(screen.getByRole("alert")).toHaveTextContent("API 不可用");
  });

  it("executes human property control directly without a confirmation UI", async () => {
    const api = new DemoApiClient();
    const write = vi.spyOn(api, "writeProperties");
    const user = userEvent.setup();
    renderWithApi(
      <Routes>
        <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
      </Routes>,
      api,
      ["/devices/device-lock"],
    );
    expect(await screen.findByRole("heading", { name: "玄关门锁" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("目标值"), "UNLOCK");
    await user.click(screen.getByRole("button", { name: "直接写入" }));
    await waitFor(() =>
      expect(write).toHaveBeenCalledWith("device-lock", { LockState: "UNLOCK" }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(/确认.*写入/)).not.toBeInTheDocument();
    expect(await screen.findByText(/执行结果：succeeded/)).toBeInTheDocument();
  });

  it("supports explicit approve and reject decisions for autonomous pending actions", async () => {
    const api = new DemoApiClient();
    const approve = vi.spyOn(api, "decideConfirmation");
    const user = userEvent.setup();
    renderWithApi(<OperationsPage />, api);

    const approveButton = await screen.findByRole("button", { name: "批准此操作" });
    expect(screen.getByText("自动任务")).toBeInTheDocument();
    await user.click(approveButton);

    await waitFor(() => expect(approve).toHaveBeenCalledWith("confirm-1", "approve", "hash-1"));
    expect(screen.getByText(/决定已提交/)).toBeInTheDocument();
  });

  it("submits an explicit reject decision for autonomous pending actions", async () => {
    const api = new DemoApiClient();
    const reject = vi.spyOn(api, "decideConfirmation");
    const user = userEvent.setup();
    renderWithApi(<OperationsPage />, api);

    await user.click(await screen.findByRole("button", { name: "拒绝此操作" }));

    await waitFor(() => expect(reject).toHaveBeenCalledWith("confirm-1", "reject", "hash-1"));
    expect(screen.getByText(/决定已提交：拒绝/)).toBeInTheDocument();
  });
});

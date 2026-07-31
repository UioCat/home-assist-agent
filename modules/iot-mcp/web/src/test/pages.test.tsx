import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiProvider } from "../api/context";
import { DemoApiClient } from "../api/demo";
import { PageState } from "../components/PageState";
import { DeviceDetailPage } from "../pages/DeviceDetailPage";
import { DevicesPage } from "../pages/DevicesPage";
import { OperationsPage } from "../pages/OperationsPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { ThingModelsPage } from "../pages/ThingModelsPage";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

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

  it("renders loading, empty, and rejected states from a real device API query", async () => {
    const loadingApi = new DemoApiClient();
    const pending = deferred<Awaited<ReturnType<DemoApiClient["listDevices"]>>>();
    vi.spyOn(loadingApi, "listDevices").mockReturnValue(pending.promise);
    const loading = renderWithApi(<DevicesPage />, loadingApi);
    expect(screen.getByRole("status")).toHaveTextContent("正在读取设备实例");
    await act(async () => pending.resolve([]));
    expect(await screen.findByText("没有符合筛选条件的设备")).toBeInTheDocument();
    loading.unmount();

    const failingApi = new DemoApiClient();
    vi.spyOn(failingApi, "listDevices").mockRejectedValue(new Error("provider offline"));
    renderWithApi(<DevicesPage />, failingApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("provider offline");
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
    expect(
      screen.getAllByText("写入 3 个属性：KeypadLock、LockState、pin"),
    ).toHaveLength(2);
    expect(screen.getByText("敏感值已隐藏")).toBeInTheDocument();
    expect(screen.queryByText(/839201/)).not.toBeInTheDocument();
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

  it("disables confirmation decisions and reports an authenticated POST error", async () => {
    const api = new DemoApiClient();
    const pending = deferred<Awaited<ReturnType<DemoApiClient["decideConfirmation"]>>>();
    vi.spyOn(api, "decideConfirmation").mockReturnValue(pending.promise);
    const user = userEvent.setup();
    renderWithApi(<OperationsPage />, api);

    await user.click(await screen.findByRole("button", { name: "批准此操作" }));
    expect(screen.getByRole("button", { name: "批准此操作" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝此操作" })).toBeDisabled();
    await act(async () => pending.reject(new Error("decision denied")));
    expect(await screen.findByText("提交失败：decision denied")).toBeInTheDocument();
  });

  it("disables service invoke and reports its POST error", async () => {
    const api = new DemoApiClient();
    const pending = deferred<Awaited<ReturnType<DemoApiClient["invokeService"]>>>();
    vi.spyOn(api, "invokeService").mockReturnValue(pending.promise);
    const user = userEvent.setup();
    renderWithApi(
      <Routes>
        <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
      </Routes>,
      api,
      ["/devices/device-lock"],
    );

    await screen.findByRole("heading", { name: "玄关门锁" });
    await user.type(screen.getByRole("spinbutton", { name: /持续秒数/ }), "10");
    await user.click(screen.getByRole("button", { name: "直接调用服务" }));
    expect(screen.getByRole("button", { name: "调用中…" })).toBeDisabled();
    await act(async () => pending.reject(new Error("invoke denied")));
    expect(await screen.findByText("执行失败：invoke denied")).toBeInTheDocument();
  });

  it("disables provider sync and reports its POST error", async () => {
    const api = new DemoApiClient();
    const pending = deferred<Awaited<ReturnType<DemoApiClient["syncProvider"]>>>();
    vi.spyOn(api, "syncProvider").mockReturnValue(pending.promise);
    const user = userEvent.setup();
    renderWithApi(<ProvidersPage />, api);

    const syncButtons = await screen.findAllByRole("button", { name: "手动同步" });
    await user.click(syncButtons[0]);
    expect(screen.getByRole("button", { name: "同步中…" })).toBeDisabled();
    await act(async () => pending.reject(new Error("sync denied")));
    expect(await screen.findByText("同步失败：sync denied")).toBeInTheDocument();
  });

  it("keeps mutable demo device state isolated per API client", async () => {
    const first = new DemoApiClient();
    const second = new DemoApiClient();

    await first.writeProperties("device-lock", { LockState: "UNLOCK" });

    expect((await first.getDeviceState("device-lock")).values.LockState).toBe("UNLOCK");
    expect((await second.getDeviceState("device-lock")).values.LockState).toBe("LOCK");
    expect((await second.listOperations())[0].operation_id).toBe("op-pending");
  });

  it("disables model validation and prevents duplicate submissions while pending", async () => {
    const api = new DemoApiClient();
    const pending = deferred<{ valid: boolean; model_version_id: string }>();
    const validate = vi.spyOn(api, "validateThingModel").mockReturnValue(pending.promise);
    const user = userEvent.setup();
    renderWithApi(<ThingModelsPage />, api);

    await user.click(await screen.findByRole("button", { name: /家庭门锁/ }));
    const button = screen.getByRole("button", { name: "校验当前版本" });
    act(() => {
      button.click();
      button.click();
    });

    await waitFor(() => expect(validate).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "正在校验…" })).toBeDisabled();
    await act(async () =>
      pending.resolve({ valid: true, model_version_id: "model-lock-v3" }),
    );
    expect(await screen.findByText(/通过标准 TSL 校验/)).toBeInTheDocument();
  });

  it("imports a JSON document as a draft from the model workbench", async () => {
    const api = new DemoApiClient();
    const importModel = vi.spyOn(api, "importThingModel");
    const user = userEvent.setup();
    renderWithApi(<ThingModelsPage />, api);

    await user.click(
      await screen.findByRole("button", { name: "导入 TSL 草稿" }),
    );
    await user.type(screen.getByLabelText("产品名称"), "测试调光器");
    await user.upload(
      screen.getByLabelText("TSL JSON 文件"),
      new File(
        [
          JSON.stringify({
            schema: "https://iotx-tsl.example/schema.json",
            profile: { productKey: "test-dimmer" },
            properties: [],
            services: [],
            events: [],
          }),
        ],
        "test-dimmer.json",
        { type: "application/json" },
      ),
    );
    await user.click(screen.getByRole("button", { name: "创建草稿" }));

    await waitFor(() =>
      expect(importModel).toHaveBeenCalledWith(
        "测试调光器",
        expect.objectContaining({
          profile: { productKey: "test-dimmer" },
        }),
      ),
    );
    expect(await screen.findByText(/草稿已导入/)).toBeInTheDocument();
  });

  it("offers export, publish, and archive actions for a selected draft", async () => {
    const api = new DemoApiClient();
    const imported = await api.importThingModel("手工门锁", {
      schema: "https://iotx-tsl.example/schema.json",
      profile: { productKey: "manual-lock" },
      properties: [],
      services: [],
      events: [],
    });
    const publish = vi
      .spyOn(api, "publishThingModel")
      .mockResolvedValue({ ...imported.model, status: "active" });
    const archive = vi
      .spyOn(api, "archiveThingModel")
      .mockResolvedValue({ ...imported.model, status: "archived" });
    const exportModel = vi
      .spyOn(api, "exportThingModel")
      .mockResolvedValue(imported.model.tsl_json);
    const downloadClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderWithApi(<ThingModelsPage />, api);

    await user.click(
      await screen.findByRole("button", { name: /手工门锁/ }),
    );
    await user.click(screen.getByRole("button", { name: "导出 JSON" }));
    await waitFor(() =>
      expect(exportModel).toHaveBeenCalledWith(
        imported.model.model_version_id,
      ),
    );
    await user.click(screen.getByRole("button", { name: "发布草稿" }));
    await waitFor(() =>
      expect(publish).toHaveBeenCalledWith(imported.model.model_version_id),
    );

    publish.mockClear();
    await user.click(screen.getByRole("button", { name: /手工门锁/ }));
    await user.click(screen.getByRole("button", { name: "归档草稿" }));
    await waitFor(() =>
      expect(archive).toHaveBeenCalledWith(imported.model.model_version_id),
    );
    downloadClick.mockRestore();
  });
});

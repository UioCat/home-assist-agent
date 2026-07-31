import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiProvider } from "../api/context";
import { DemoApiClient } from "../api/demo";
import { DeviceDetailPage } from "../pages/DeviceDetailPage";
import { EventsPage } from "../pages/EventsPage";
import { MessageChannelsPage } from "../pages/MessageChannelsPage";
import { OperationsPage } from "../pages/OperationsPage";
import { OverviewPage } from "../pages/OverviewPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { ThingModelsPage } from "../pages/ThingModelsPage";

interface StateCase {
  name: string;
  ui: React.ReactNode;
  path?: string;
  configure(api: DemoApiClient): void;
  role: "status" | "alert";
  expected: string;
}

const never = new Promise<never>(() => undefined);
const cases: StateCase[] = [
  {
    name: "overview loading",
    ui: <OverviewPage />,
    configure(api) {
      vi.spyOn(api, "listDevices").mockReturnValue(never);
    },
    role: "status",
    expected: "正在汇总家庭设备信号",
  },
  {
    name: "thing models empty",
    ui: <ThingModelsPage />,
    configure(api) {
      vi.spyOn(api, "listThingModels").mockResolvedValue([]);
    },
    role: "status",
    expected: "尚未导入物模型",
  },
  {
    name: "device detail error",
    ui: (
      <Routes>
        <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
      </Routes>
    ),
    path: "/devices/device-lock",
    configure(api) {
      vi.spyOn(api, "getDevice").mockRejectedValue(new Error("detail unavailable"));
    },
    role: "alert",
    expected: "detail unavailable",
  },
  {
    name: "operations empty",
    ui: <OperationsPage />,
    configure(api) {
      vi.spyOn(api, "listOperations").mockResolvedValue([]);
      vi.spyOn(api, "listConfirmations").mockResolvedValue([]);
    },
    role: "status",
    expected: "当前没有待确认操作",
  },
  {
    name: "events error",
    ui: <EventsPage />,
    configure(api) {
      vi.spyOn(api, "listEvents").mockRejectedValue(new Error("events unavailable"));
    },
    role: "alert",
    expected: "events unavailable",
  },
  {
    name: "providers loading",
    ui: <ProvidersPage />,
    configure(api) {
      vi.spyOn(api, "listProviders").mockReturnValue(never);
    },
    role: "status",
    expected: "正在诊断 Provider",
  },
  {
    name: "message channels empty",
    ui: <MessageChannelsPage />,
    configure(api) {
      vi.spyOn(api, "listMessageChannels").mockResolvedValue([]);
    },
    role: "status",
    expected: "没有已注册消息通道",
  },
];

describe.each(cases)("$name", ({ ui, path = "/", configure, role, expected }) => {
  it(`renders a real ${role} branch`, async () => {
    const api = new DemoApiClient();
    configure(api);
    render(
      <ApiProvider api={api}>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </ApiProvider>,
    );

    const content = await screen.findByText(expected);
    expect(content.closest(`[role="${role}"]`)).toHaveTextContent(expected);
  });
});

import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiProvider } from "../api/context";
import { DemoApiClient } from "../api/demo";
import { AppShell } from "../components/AppShell";
import { DeviceDetailPage } from "../pages/DeviceDetailPage";
import { DevicesPage } from "../pages/DevicesPage";
import { EventsPage } from "../pages/EventsPage";
import { MessageChannelsPage } from "../pages/MessageChannelsPage";
import { OperationsPage } from "../pages/OperationsPage";
import { OverviewPage } from "../pages/OverviewPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { ThingModelsPage } from "../pages/ThingModelsPage";

const routes = [
  ["/", "先处理失联与待确认"],
  ["/thing-models", "物模型"],
  ["/devices", "设备实例"],
  ["/devices/device-lock", "玄关门锁"],
  ["/operations", "操作与确认"],
  ["/events", "设备事件"],
  ["/providers", "Provider"],
  ["/message-channels", "消息通道"],
] as const;

describe.each(routes)("route %s", (path, heading) => {
  it(`renders ${heading}`, async () => {
    render(
      <ApiProvider api={new DemoApiClient()}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<AppShell demo />}>
              <Route index element={<OverviewPage />} />
              <Route path="thing-models" element={<ThingModelsPage />} />
              <Route path="devices" element={<DevicesPage />} />
              <Route path="devices/:deviceId" element={<DeviceDetailPage />} />
              <Route path="operations" element={<OperationsPage />} />
              <Route path="events" element={<EventsPage />} />
              <Route path="providers" element={<ProvidersPage />} />
              <Route path="message-channels" element={<MessageChannelsPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ApiProvider>,
    );

    expect(await screen.findByRole("heading", { name: heading, level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeInTheDocument();
  });
});

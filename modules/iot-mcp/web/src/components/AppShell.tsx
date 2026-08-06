import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { consolePath } from "./routing";

const navigation = [
  { to: "/", label: "家庭指令中心", mark: "01" },
  { to: "/audit", label: "审计中心", mark: "02" },
  { to: "/overview", label: "家庭概览", mark: "03" },
  { to: "/devices", label: "设备", mark: "04" },
  { to: "/thing-models", label: "设备能力", mark: "05" },
  { to: "/operations", label: "待确认与记录", mark: "06" },
  { to: "/events", label: "设备动态", mark: "07" },
  { to: "/providers", label: "设备来源", mark: "08" },
  { to: "/message-channels", label: "通知渠道", mark: "09" },
];

export function AppShell({
  demo,
  iotUnavailable = false,
}: {
  demo: boolean;
  authEnabled?: boolean;
  iotUnavailable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className="mobile-header">
        <span className="brand"><span className="brand__signal" />家庭控制台</span>
        <button
          className="nav-toggle"
          type="button"
          aria-expanded={open}
          aria-controls="primary-navigation"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "关闭导航" : "打开导航"}
        </button>
      </header>
      <aside className={`sidebar${open ? " sidebar--open" : ""}`}>
        <div className="sidebar__brand">
          <span className="brand__signal" aria-hidden="true" />
          <div><strong>家庭控制台</strong><span>家庭设备与自动化</span></div>
        </div>
        {demo ? (
          <div className="demo-banner" role="status">
            <strong>演示数据</strong>
            <span>与真实 API 隔离；不会控制设备</span>
          </div>
        ) : null}
        <nav id="primary-navigation" aria-label="主导航">
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={consolePath(item.to)}
              end={item.to === "/"}
              onClick={() => setOpen(false)}
              className={({ isActive }) => (isActive ? "nav-link nav-link--active" : "nav-link")}
            >
              <span className="mono nav-link__mark">{item.mark}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className={`sidebar__health${iotUnavailable ? " sidebar__health--offline" : ""}`}>
          <span className="sidebar__health-dot" aria-hidden="true" />
          <div>
            <strong>
              {iotUnavailable
                ? "设备服务未连接"
                : "家庭服务正常"}
            </strong>
            <span>
              {iotUnavailable
                ? "设备控制暂不可用"
                : "设备控制可用"}
            </span>
          </div>
        </div>
      </aside>
      <main id="main-content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}

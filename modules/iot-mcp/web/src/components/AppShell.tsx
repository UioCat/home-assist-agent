import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { consolePath } from "./routing";

const navigation = [
  { to: "/", label: "概览", mark: "01" },
  { to: "/thing-models", label: "物模型", mark: "02" },
  { to: "/devices", label: "设备实例", mark: "03" },
  { to: "/operations", label: "操作与确认", mark: "04" },
  { to: "/events", label: "设备事件", mark: "05" },
  { to: "/providers", label: "Provider", mark: "06" },
  { to: "/message-channels", label: "消息通道", mark: "07" },
];

export function AppShell({ demo }: { demo: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className="mobile-header">
        <span className="brand"><span className="brand__signal" />居家控制平面</span>
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
          <div><strong>居家控制平面</strong><span>IoT MCP / OWNER</span></div>
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
        <div className="sidebar__health">
          <span className="sidebar__health-dot" aria-hidden="true" />
          <div><strong>Session 已连接</strong><span>Cookie + CSRF</span></div>
        </div>
      </aside>
      <main id="main-content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}

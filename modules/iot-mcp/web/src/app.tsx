import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { ApiProvider } from "./api/context";
import { DemoApiClient } from "./api/demo";
import { HttpApiClient } from "./api/client";
import type { IoTApi } from "./api/types";
import { AppShell } from "./components/AppShell";
import { DeviceDetailPage } from "./pages/DeviceDetailPage";
import { DevicesPage } from "./pages/DevicesPage";
import { EventsPage } from "./pages/EventsPage";
import { MessageChannelsPage } from "./pages/MessageChannelsPage";
import { OperationsPage } from "./pages/OperationsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { ThingModelsPage } from "./pages/ThingModelsPage";

type SessionState = "checking" | "authenticated" | "unauthenticated";

export function App({
  api: suppliedApi,
  demo: suppliedDemo,
}: {
  api?: IoTApi;
  demo?: boolean;
  } = {}) {
  const demo =
    suppliedDemo ??
    (new URLSearchParams(window.location.search).get("demo") === "1");
  const [sessionState, setSessionState] = useState<SessionState>(
    demo ? "authenticated" : "checking",
  );
  const api = useMemo<IoTApi>(
    () =>
      suppliedApi ??
      (demo ? new DemoApiClient() : new HttpApiClient()),
    [demo, suppliedApi],
  );

  useEffect(() => {
    if (demo) {
      setSessionState("authenticated");
      return;
    }
    let active = true;
    const unsubscribe = api.onSessionInvalid(() => {
      if (active) setSessionState("unauthenticated");
    });
    setSessionState("checking");
    api.bootstrapSession().then(
      () => {
        if (active) setSessionState("authenticated");
      },
      () => {
        if (active) setSessionState("unauthenticated");
      },
    );
    return () => {
      active = false;
      unsubscribe();
    };
  }, [api, demo]);

  return (
    <ApiProvider api={api}>
      {sessionState === "checking" ? (
        <SessionLoading />
      ) : sessionState === "unauthenticated" ? (
        <SessionGate api={api} onAuthenticated={() => setSessionState("authenticated")} />
      ) : (
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell demo={demo} />}>
              <Route index element={<OverviewPage />} />
              <Route path="thing-models" element={<ThingModelsPage />} />
              <Route path="devices" element={<DevicesPage />} />
              <Route path="devices/:deviceId" element={<DeviceDetailPage />} />
              <Route path="operations" element={<OperationsPage />} />
              <Route path="events" element={<EventsPage />} />
              <Route path="providers" element={<ProvidersPage />} />
              <Route path="message-channels" element={<MessageChannelsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      )}
    </ApiProvider>
  );
}

function SessionLoading() {
  return (
    <main className="session-screen">
      <section className="session-panel" aria-live="polite">
        <p className="eyebrow">IOT MCP / SECURE SESSION</p>
        <h1>正在恢复安全 Session</h1>
        <p>正在验证浏览器中的短期 HttpOnly Cookie。</p>
      </section>
    </main>
  );
}

function SessionGate({
  api,
  onAuthenticated,
}: {
  api: IoTApi;
  onAuthenticated: () => void;
}) {
  const [adminToken, setAdminToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.createSession(adminToken);
      onAuthenticated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法建立 Session");
    } finally {
      setAdminToken("");
      setBusy(false);
    }
  }

  return (
    <main className="session-screen">
      <section className="session-panel" aria-labelledby="session-title">
        <div className="session-panel__mark" aria-hidden="true"><span /><i /><span /></div>
        <p className="eyebrow">IOT MCP / SECURE SESSION</p>
        <h1 id="session-title">连接家庭设备控制台</h1>
        <p>Admin Token 仅用于一次 POST 换取短期 Session。浏览器不会把 Token 写入本地存储。</p>
        <form onSubmit={submit}>
          <label htmlFor="admin-token"><span>Admin Token</span><input id="admin-token" type="password" autoComplete="off" value={adminToken} onChange={(event) => setAdminToken(event.target.value)} required /></label>
          <button className="button button--primary" type="submit" disabled={busy || !adminToken}>{busy ? "正在建立 Session…" : "建立安全 Session"}</button>
        </form>
        <p className="session-error" role="alert" aria-live="polite">{error}</p>
        <div className="session-footnote"><span>Cookie</span><strong>HttpOnly · SameSite</strong><span>写操作</span><strong>CSRF bound</strong></div>
        <a className="demo-link" href="/?demo=1">打开隔离演示数据</a>
      </section>
    </main>
  );
}

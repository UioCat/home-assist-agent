import type { AgentHealth as AgentHealthData } from "../api/agent";

export function AgentHealth({
  health,
  unavailable = false,
}: {
  health: AgentHealthData | null;
  unavailable?: boolean;
}) {
  if (unavailable) {
    return (
      <div className="agent-health" aria-label="主流程依赖状态">
        <HealthPill tone="danger">主流程未连接</HealthPill>
      </div>
    );
  }
  if (!health) {
    return (
      <div className="agent-health" aria-label="主流程依赖状态">
        <HealthPill tone="muted">正在检查主流程</HealthPill>
      </div>
    );
  }
  const codexReady = health.codex.installed && health.codex.authenticated;
  return (
    <div className="agent-health" aria-label="主流程依赖状态">
      <HealthPill tone={codexReady ? "ok" : "danger"}>
        {codexReady ? "智能助手可用" : "智能助手未就绪"}
      </HealthPill>
      <HealthPill tone={health.ha_mcp.connected ? "ok" : "warning"}>
        {health.ha_mcp.connected
          ? "设备服务已连接"
          : "设备服务未连接"}
      </HealthPill>
    </div>
  );
}

function HealthPill({
  tone,
  children,
}: {
  tone: "ok" | "warning" | "danger" | "muted";
  children: React.ReactNode;
}) {
  return (
    <span className={`agent-health__pill agent-health__pill--${tone}`}>
      <span aria-hidden="true" />
      {children}
    </span>
  );
}

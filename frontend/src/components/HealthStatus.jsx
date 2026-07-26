function StatusPill({ tone, children }) {
  return (
    <span className={`status-pill status-pill--${tone}`}>
      <span className="status-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

export default function HealthStatus({ health }) {
  if (!health) {
    return (
      <div className="health-strip" aria-label="依赖状态">
        <StatusPill tone="pending">正在检查依赖</StatusPill>
      </div>
    );
  }

  if (health.unavailable) {
    return (
      <div className="health-strip" aria-label="依赖状态">
        <StatusPill tone="error">后端未连接</StatusPill>
      </div>
    );
  }

  const codexReady =
    health.codex?.installed && health.codex?.authenticated;
  const haReady = health.ha_mcp?.connected;

  return (
    <div className="health-strip" aria-label="依赖状态">
      <StatusPill tone={codexReady ? "online" : "error"}>
        {codexReady ? "Codex 已就绪" : "Codex 未就绪"}
      </StatusPill>
      <StatusPill tone={haReady ? "online" : "pending"}>
        {haReady
          ? `HA MCP · ${health.ha_mcp.tool_count} 个工具`
          : "HA MCP 未连接"}
      </StatusPill>
    </div>
  );
}

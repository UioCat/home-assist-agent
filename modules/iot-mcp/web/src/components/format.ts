export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function actionSummary(action?: Record<string, unknown> | null): string {
  if (!action) return "无动作摘要";
  const type = String(action.type ?? "action");
  if (type === "properties") {
    return `写入属性 ${JSON.stringify(action.values ?? {})}`;
  }
  if (type === "service") {
    return `调用服务 ${String(action.identifier ?? "unknown")} ${JSON.stringify(action.inputs ?? {})}`;
  }
  return JSON.stringify(action);
}

export function humanize(value: string): string {
  const labels: Record<string, string> = {
    active: "正常",
    missing: "失联",
    healthy: "健康",
    degraded: "降级",
    configured: "已配置",
    not_configured: "未配置",
    fresh: "新鲜",
    stale: "陈旧",
    unknown: "未知",
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    pending_confirmation: "等待确认",
    succeeded: "成功",
    failed: "失败",
    rejected: "已拒绝",
    approved: "已批准",
    expired: "已过期",
    executing: "执行中",
    accepted: "已接受",
    no_op: "无变化",
    human_interactive: "人工直控",
    autonomous: "自动来源",
  };
  return labels[value] ?? value.replaceAll("_", " ");
}

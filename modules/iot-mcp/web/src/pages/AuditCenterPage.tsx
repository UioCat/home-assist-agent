import { useCallback, useEffect, useState } from "react";

import type { AgentAuditEvent, AgentAuditMessage } from "../api/agent";
import { useAgentApi } from "../api/agentContext";
import { PageHeader } from "../components/PageHeader";

const eventLabels: Record<string, { title: string; kind: string }> = {
  "user.request": { title: "用户 → 系统", kind: "用户输入" },
  "user.response": { title: "系统 → 用户", kind: "最终回复" },
  "codex.request": { title: "系统 → Codex", kind: "模型请求" },
  "codex.response": { title: "Codex → 系统", kind: "模型响应" },
  "external.request": { title: "系统 → 外部服务", kind: "服务请求" },
  "external.response": { title: "外部服务 → 系统", kind: "服务响应" },
  "event.received": { title: "事件源 → 系统", kind: "事件输入" },
  "event.duplicate": { title: "系统去重", kind: "幂等检查" },
  "event.response": { title: "系统 → 事件源", kind: "事件回执" },
  "context.update.request": { title: "事件编排 → 家庭上下文", kind: "上下文写入" },
  "context.update.response": { title: "家庭上下文 → 事件编排", kind: "上下文结果" },
  "automation.no_match": { title: "自动化规则：未命中", kind: "规则判断" },
  "automation.matched": { title: "自动化规则：已命中", kind: "派生意图" },
  "automation.result": { title: "指令编排 → 自动化", kind: "执行结果" },
};

const statusLabels: Record<string, string> = {
  success: "成功",
  blocked: "已阻止",
  error: "失败",
};

function formatTime(value: string, includeDate = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: includeDate ? "2-digit" : undefined,
    day: includeDate ? "2-digit" : undefined,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function payloadText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function eventSummary(event: AgentAuditEvent) {
  const payload = event.payload;
  if (event.event_type === "user.request") return payloadText(payload.command) ?? "收到用户消息";
  if (event.event_type === "user.response") return payloadText(payload.message) ?? payloadText(payload.error) ?? "已生成用户响应";
  if (event.event_type === "codex.request") return payloadText(payload.prompt) ?? "已向 Codex 发送请求";
  if (event.event_type === "codex.response") return payloadText(payload.output) ?? payloadText(payload.error) ?? "Codex 已返回";
  if (event.event_type === "external.request") return `${payloadText(payload.operation) ?? "request"}${payload.tool_name ? ` · ${String(payload.tool_name)}` : ""}`;
  if (event.event_type === "external.response") return payloadText(payload.content) ?? payloadText(payload.error) ?? payloadText(payload.operation) ?? "外部服务已返回";
  if (event.event_type === "event.received") return `${payloadText(payload.event_type) ?? "未知事件"} · ${payloadText(payload.subject_id) ?? "未知对象"}${payload.location ? ` · ${String(payload.location)}` : ""}`;
  if (event.event_type === "automation.no_match") return "未命中显式规则，仅更新上下文；不调用 Codex 或 Home Assistant。";
  return event.event_type;
}

function AuditEventItem({ event }: { event: AgentAuditEvent }) {
  const label = eventLabels[event.event_type] ?? {
    title: event.event_type,
    kind: "审计事件",
  };
  return (
    <li className={`agent-audit-event agent-audit-event--${event.status}`}>
      <div className="agent-audit-event__rail" aria-hidden="true">
        <span>{String(event.sequence).padStart(2, "0")}</span><i />
      </div>
      <article>
        <header>
          <div><p>{label.kind}</p><h3>{label.title}</h3></div>
          <div className="agent-audit-event__meta">
            <span>{statusLabels[event.status] ?? event.status}</span>
            <time dateTime={event.created_at}>{formatTime(event.created_at)}</time>
          </div>
        </header>
        <p className="agent-audit-event__summary">{eventSummary(event)}</p>
        <div className="agent-audit-event__context">
          <code><span>service</span>{event.service}</code>
          {event.correlation_id ? <code><span>correlation</span>{event.correlation_id}</code> : null}
          {event.causation_id ? <code><span>causation</span>{event.causation_id}</code> : null}
          {event.error_code ? <code className="error-code">{event.error_code}</code> : null}
        </div>
        <details><summary>查看完整 Payload</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>
      </article>
    </li>
  );
}

export function AuditCenterPage() {
  const api = useAgentApi();
  const [messages, setMessages] = useState<AgentAuditMessage[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [events, setEvents] = useState<AgentAuditEvent[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(true);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [error, setError] = useState("");

  const loadMessages = useCallback(async () => {
    setLoadingMessages(true);
    setError("");
    try {
      const payload = await api.listAuditMessages();
      setMessages(payload);
      setSelectedId((current) =>
        payload.some((message) => message.message_id === current)
          ? current
          : payload[0]?.message_id ?? "",
      );
    } catch {
      setError("无法读取主流程审计，请确认 8080 端口和审计数据库可用。");
    } finally {
      setLoadingMessages(false);
    }
  }, [api]);

  useEffect(() => { void loadMessages(); }, [loadMessages]);

  useEffect(() => {
    let active = true;
    if (!selectedId) {
      setEvents([]);
      return () => { active = false; };
    }
    setLoadingEvents(true);
    api.listAuditEvents(selectedId).then(
      (payload) => { if (active) setEvents(payload); },
      () => { if (active) setError("无法读取该消息的审计链路。"); },
    ).finally(() => { if (active) setLoadingEvents(false); });
    return () => { active = false; };
  }, [api, selectedId]);

  const selected = messages.find((message) => message.message_id === selectedId);
  return (
    <div className="page agent-audit-page">
      <PageHeader
        eyebrow="LOCAL FLIGHT RECORDER"
        title="审计中心"
        description="按消息或事件还原用户、Codex、家庭上下文与外部服务之间的完整请求响应链路。凭据在写入前统一脱敏。"
        actions={<button className="button button--secondary" type="button" onClick={loadMessages} disabled={loadingMessages}>{loadingMessages ? "刷新中…" : "刷新记录"}</button>}
      />
      {error ? <div className="agent-audit-error" role="alert">{error}</div> : null}
      <div className="agent-audit-layout">
        <aside className="agent-audit-index" aria-label="审计消息列表">
          <header><div><span>消息索引</span><strong>{messages.length}</strong></div><small>最近 50 条</small></header>
          {loadingMessages ? (
            <div className="agent-audit-state">正在读取审计消息…</div>
          ) : messages.length ? (
            <ol>
              {messages.map((message) => (
                <li key={message.message_id}>
                  <button type="button" className={message.message_id === selectedId ? "active" : ""} aria-pressed={message.message_id === selectedId} onClick={() => setSelectedId(message.message_id)}>
                    <span><b>{message.input_type === "event" ? "事件" : "消息"}</b><code>{message.message_id.slice(0, 12)}</code><time dateTime={message.ended_at}>{formatTime(message.ended_at, true)}</time></span>
                    <strong>{message.command ?? "系统事件"}</strong>
                    <span><em>{statusLabels[message.status] ?? message.status}</em><span>{message.event_count} 个事件</span></span>
                  </button>
                </li>
              ))}
            </ol>
          ) : <div className="agent-audit-state"><strong>还没有审计记录</strong><span>执行一条指令后，这里会出现完整链路。</span></div>}
        </aside>
        <section className="agent-audit-trace" aria-label="消息审计链路">
          {selected ? (
            <>
              <header>
                <div><p>{selected.input_type === "event" ? "EVENT TRACE" : "MESSAGE TRACE"}</p><h2>{selected.command ?? "系统事件"}</h2></div>
                <div><span>MESSAGE ID</span><code>{selected.message_id}</code></div>
              </header>
              {loadingEvents ? <div className="agent-audit-state">正在拼接消息链路…</div> : <ol>{events.map((event) => <AuditEventItem event={event} key={event.event_id} />)}</ol>}
            </>
          ) : <div className="agent-audit-state"><strong>等待审计消息</strong><span>选择左侧消息后，这里会显示完整请求响应轨道。</span></div>}
        </section>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";

import { getAuditEvents, getAuditMessages } from "../api";


const eventLabels = {
  "user.request": {
    title: "用户 → 系统",
    kind: "用户输入",
  },
  "user.response": {
    title: "系统 → 用户",
    kind: "最终回复",
  },
  "codex.request": {
    title: "系统 → Codex",
    kind: "模型请求",
  },
  "codex.response": {
    title: "Codex → 系统",
    kind: "模型响应",
  },
  "external.request": {
    title: "系统 → 外部服务",
    kind: "服务请求",
  },
  "external.response": {
    title: "外部服务 → 系统",
    kind: "服务响应",
  },
  "event.received": {
    title: "事件源 → 系统",
    kind: "事件输入",
  },
  "event.duplicate": {
    title: "系统去重",
    kind: "幂等检查",
  },
  "event.response": {
    title: "系统 → 事件源",
    kind: "事件回执",
  },
  "context.update.request": {
    title: "事件编排 → 家庭上下文",
    kind: "上下文写入",
  },
  "context.update.response": {
    title: "家庭上下文 → 事件编排",
    kind: "上下文结果",
  },
  "automation.no_match": {
    title: "自动化规则：未命中",
    kind: "规则判断",
  },
  "automation.matched": {
    title: "自动化规则：已命中",
    kind: "派生意图",
  },
  "automation.result": {
    title: "指令编排 → 自动化",
    kind: "执行结果",
  },
};

const statusLabels = {
  success: "成功",
  blocked: "已阻止",
  error: "失败",
};

const eventStatusLabels = {
  observed: "已记录，不执行设备",
  duplicate: "重复事件，已忽略",
  triggered: "已触发自动化",
};

const inputTypeLabels = {
  message: "消息",
  event: "事件",
};

const codexPurposeLabels = {
  route: "意图路由",
  device_plan: "设备规划",
  answer: "普通回答",
};

function formatTime(value, includeDate = false) {
  if (!value) return "—";
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

function eventSummary(event) {
  const payload = event.payload || {};
  if (event.event_type === "user.request") {
    return payload.command || "收到用户消息";
  }
  if (event.event_type === "user.response") {
    return payload.message || payload.error || "已生成用户响应";
  }
  if (event.event_type === "codex.request") {
    return payload.prompt || "已向 Codex 发送请求";
  }
  if (event.event_type === "codex.response") {
    return (
      payload.structured_output?.message
      || payload.output
      || payload.error
      || "Codex 已返回"
    );
  }
  if (event.event_type === "external.request") {
    const target = payload.tool_name ? ` · ${payload.tool_name}` : "";
    return `${payload.operation || "request"}${target}`;
  }
  if (event.event_type === "external.response") {
    return (
      payload.content
      || payload.error
      || payload.operation
      || "外部服务已返回"
    );
  }
  if (event.event_type === "event.received") {
    const location = payload.location ? ` · ${payload.location}` : "";
    return `${payload.event_type || "未知事件"} · ${
      payload.subject_id || "未知对象"
    }${location}`;
  }
  if (event.event_type === "event.duplicate") {
    return `${payload.source || "未知来源"} · ${
      payload.event_id || "未知事件 ID"
    }`;
  }
  if (event.event_type === "event.response") {
    return eventStatusLabels[payload.status] || payload.error || payload.status;
  }
  if (
    event.event_type === "context.update.request"
    || event.event_type === "context.update.response"
  ) {
    const location = payload.location ? ` → ${payload.location}` : "";
    return `${payload.subject_id || "未知对象"} · ${
      payload.event_type || "上下文"
    }${location}`;
  }
  if (event.event_type === "automation.no_match") {
    return "未命中显式规则，仅更新上下文；不调用 Codex 或 Home Assistant。";
  }
  if (event.event_type === "automation.matched") {
    return `${payload.rule_id || "已配置规则"} · ${
      payload.prompt || "已生成派生设备意图"
    }`;
  }
  if (event.event_type === "automation.result") {
    return payload.message || payload.error || "自动化指令已处理";
  }
  return event.event_type;
}

function eventLabel(event) {
  const payload = event.payload || {};
  if (
    event.event_type === "external.request"
    || event.event_type === "external.response"
  ) {
    if (payload.operation === "list_tools") {
      return {
        title:
          event.event_type === "external.request"
            ? "系统 → HA 工具目录"
            : "HA 工具目录 → 系统",
        kind: "能力查询",
      };
    }
    if (payload.operation === "call_tool") {
      return {
        title:
          event.event_type === "external.request"
            ? "系统 → Home Assistant"
            : "Home Assistant → 系统",
        kind: "设备调用",
      };
    }
  }
  return eventLabels[event.event_type] || {
    title: event.event_type,
    kind: "审计事件",
  };
}

function eventContext(event) {
  const payload = event.payload || {};
  const entries = [{ label: "service", value: event.service }];
  if (payload.purpose) {
    entries.push({
      label: "purpose",
      value: codexPurposeLabels[payload.purpose] || payload.purpose,
    });
  }
  if (event.event_type === "codex.request" && payload.reasoning) {
    entries.push({ label: "reasoning", value: payload.reasoning });
  }
  if (payload.operation) {
    entries.push({ label: "operation", value: payload.operation });
  }
  if (event.correlation_id) {
    entries.push({
      label: "correlation",
      value: event.correlation_id,
    });
  }
  if (event.causation_id) {
    entries.push({
      label: "causation",
      value: event.causation_id,
    });
  }
  return entries;
}

function AuditEventItem({ event }) {
  const label = eventLabel(event);
  const isAlert = event.status === "blocked" || event.status === "error";

  return (
    <li className={`audit-event audit-event--${event.status}`}>
      <div className="audit-event-rail" aria-hidden="true">
        <span>{String(event.sequence).padStart(2, "0")}</span>
        <i />
      </div>
      <article className="audit-event-card">
        <header>
          <div>
            <p>{label.kind}</p>
            <h3>{label.title}</h3>
          </div>
          <div className="audit-event-meta">
            <span className={`status-badge status-badge--${event.status}`}>
              {statusLabels[event.status] || event.status}
            </span>
            <time dateTime={event.created_at}>
              {formatTime(event.created_at)}
            </time>
          </div>
        </header>

        <p className="audit-event-summary">{eventSummary(event)}</p>

        <div className="audit-event-context">
          {eventContext(event).map((item) => (
            <code key={item.label}>
              <span>{item.label}</span>
              {item.value}
            </code>
          ))}
          {event.error_code ? (
            <code className="error-code">{event.error_code}</code>
          ) : null}
        </div>

        <details className="audit-payload">
          <summary>查看完整 Payload</summary>
          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
        </details>
        {isAlert ? <span className="sr-only">该事件需要关注</span> : null}
      </article>
    </li>
  );
}

export default function AuditCenter() {
  const [messages, setMessages] = useState([]);
  const [selectedMessageId, setSelectedMessageId] = useState("");
  const [events, setEvents] = useState([]);
  const [isLoadingMessages, setIsLoadingMessages] = useState(true);
  const [isLoadingEvents, setIsLoadingEvents] = useState(false);
  const [error, setError] = useState("");

  const loadMessages = useCallback(async () => {
    setIsLoadingMessages(true);
    setError("");
    try {
      const payload = await getAuditMessages();
      setMessages(payload);
      setSelectedMessageId((current) => {
        if (payload.some((message) => message.message_id === current)) {
          return current;
        }
        return payload[0]?.message_id || "";
      });
    } catch {
      setError("无法读取审计消息，请确认本地服务和审计数据库可用。");
    } finally {
      setIsLoadingMessages(false);
    }
  }, []);

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  useEffect(() => {
    let active = true;
    if (!selectedMessageId) {
      setEvents([]);
      return () => {
        active = false;
      };
    }

    setIsLoadingEvents(true);
    setError("");
    getAuditEvents(selectedMessageId)
      .then((payload) => {
        if (active) setEvents(payload);
      })
      .catch(() => {
        if (active) {
          setEvents([]);
          setError("无法读取该消息的审计链路。");
        }
      })
      .finally(() => {
        if (active) setIsLoadingEvents(false);
      });
    return () => {
      active = false;
    };
  }, [selectedMessageId]);

  const selectedMessage = messages.find(
    (message) => message.message_id === selectedMessageId,
  );

  return (
    <main className="audit-workspace">
      <section className="audit-intro" aria-labelledby="audit-title">
        <div>
          <p className="eyebrow">LOCAL FLIGHT RECORDER</p>
          <h1 id="audit-title">审计中心</h1>
          <p>
            按消息或事件还原用户、Codex、家庭上下文与外部服务之间的
            完整请求响应链路。凭据已在写入前脱敏。
          </p>
        </div>
        <button
          className="audit-refresh"
          type="button"
          onClick={loadMessages}
          disabled={isLoadingMessages}
        >
          {isLoadingMessages ? "刷新中…" : "刷新记录"}
        </button>
      </section>

      {error ? (
        <div className="audit-error" role="alert">
          {error}
        </div>
      ) : null}

      <div className="audit-layout">
        <aside className="audit-index" aria-label="审计消息列表">
          <header>
            <div>
              <span>消息索引</span>
              <strong>{messages.length}</strong>
            </div>
            <small>最近 50 条</small>
          </header>

          {isLoadingMessages ? (
            <div className="audit-list-state">正在读取审计消息…</div>
          ) : messages.length ? (
            <ol className="audit-message-list">
              {messages.map((message) => (
                <li key={message.message_id}>
                  <button
                    type="button"
                    className={
                      message.message_id === selectedMessageId
                        ? "audit-message audit-message--active"
                        : "audit-message"
                    }
                    aria-pressed={message.message_id === selectedMessageId}
                    onClick={() => setSelectedMessageId(message.message_id)}
                  >
                    <span className="audit-message-topline">
                      <span>
                        <b>{inputTypeLabels[message.input_type] || "消息"}</b>
                        <code>{message.message_id.slice(0, 12)}</code>
                      </span>
                      <time dateTime={message.ended_at}>
                        {formatTime(message.ended_at, true)}
                      </time>
                    </span>
                    <strong>{message.command || "系统事件"}</strong>
                    <span className="audit-message-bottomline">
                      <span
                        className={`audit-status audit-status--${message.status}`}
                      >
                        {statusLabels[message.status] || message.status}
                      </span>
                      <span>{message.event_count} 个事件</span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <div className="audit-list-state">
              <strong>还没有审计记录</strong>
              <span>在指令中心执行一条消息后，这里会出现完整链路。</span>
            </div>
          )}
        </aside>

        <section className="audit-trace" aria-label="消息审计链路">
          {selectedMessage ? (
            <>
              <header className="audit-trace-header">
                <div>
                  <p>
                    {selectedMessage.input_type === "event"
                      ? "EVENT TRACE"
                      : "MESSAGE TRACE"}
                  </p>
                  <h2>{selectedMessage.command || "系统事件"}</h2>
                </div>
                <div className="audit-trace-identity">
                  <div>
                    <span>MESSAGE ID</span>
                    <code>{selectedMessage.message_id}</code>
                  </div>
                  {selectedMessage.correlation_id ? (
                    <div>
                      <span>CORRELATION ID</span>
                      <code>{selectedMessage.correlation_id}</code>
                    </div>
                  ) : null}
                </div>
              </header>

              {isLoadingEvents ? (
                <div className="audit-trace-state">正在拼接消息链路…</div>
              ) : (
                <ol className="audit-event-list">
                  {events.map((event) => (
                    <AuditEventItem event={event} key={event.event_id} />
                  ))}
                </ol>
              )}
            </>
          ) : (
            <div className="audit-trace-state">
              <span className="empty-index">TRACE</span>
              <div>
                <h2>等待审计消息</h2>
                <p>选择左侧消息后，这里会显示完整请求响应轨道。</p>
              </div>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

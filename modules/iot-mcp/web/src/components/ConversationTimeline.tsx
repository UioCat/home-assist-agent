import type { AgentConversationMessage } from "../api/agent";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function ConversationTimeline({
  messages,
  loading,
}: {
  messages: AgentConversationMessage[];
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="conversation-state" role="status">
        <span className="loader" aria-hidden="true" />
        <div>
          <strong>正在恢复对话</strong>
          <span>读取之前的家庭指令与真实执行结果。</span>
        </div>
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="conversation-state conversation-state--empty">
        <span className="home-pulse" aria-hidden="true" />
        <div>
          <strong>从一句自然的指令开始</strong>
          <span>之后可以直接说“再暗一点”或“把刚才那个关掉”。</span>
        </div>
      </div>
    );
  }

  return (
    <ol className="conversation-timeline" aria-label="当前会话记录">
      {messages.map((item) => (
        <li key={item.message_id}>
          <span className="conversation-timeline__pulse" aria-hidden="true" />
          <article>
            <header>
              <strong>你</strong>
              <time dateTime={item.created_at}>{formatTime(item.created_at)}</time>
            </header>
            <p className="conversation-timeline__command">{item.command}</p>
            <div
              className={`conversation-timeline__reply conversation-timeline__reply--${item.response?.status ?? item.status}`}
            >
              <span aria-hidden="true" />
              <div>
                <strong>家庭助理</strong>
                <p>
                  {item.response?.message ??
                    (item.status === "failed" ? "这条指令没有执行成功。" : "处理中…")}
                </p>
                {item.response?.warnings.map((warning) => (
                  <small key={warning}>{warning}</small>
                ))}
              </div>
            </div>
          </article>
        </li>
      ))}
    </ol>
  );
}

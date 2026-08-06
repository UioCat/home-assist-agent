import { useEffect, useState } from "react";

import type {
  AgentConversation,
  AgentConversationMessage,
  AgentHealth as AgentHealthData,
} from "../api/agent";
import { useAgentApi } from "../api/agentContext";
import { AgentHealth } from "../components/AgentHealth";
import { CommandWorkbench } from "../components/CommandWorkbench";
import { ConversationTimeline } from "../components/ConversationTimeline";
import { PageHeader } from "../components/PageHeader";

export function CommandCenterPage() {
  const api = useAgentApi();
  const [health, setHealth] = useState<AgentHealthData | null>(null);
  const [healthUnavailable, setHealthUnavailable] = useState(false);
  const [command, setCommand] = useState("");
  const [conversation, setConversation] = useState<AgentConversation | null>(null);
  const [conversationLoading, setConversationLoading] = useState(true);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let active = true;
    api.getHealth().then(
      (value) => {
        if (active) setHealth(value);
      },
      () => {
        if (active) setHealthUnavailable(true);
      },
    );
    api.getCurrentConversation().then(
      (value) => {
        if (active) {
          setConversation(value);
          setConversationLoading(false);
        }
      },
      () => {
        if (active) {
          setError("无法恢复之前的会话，可以稍后重试。");
          setConversationLoading(false);
        }
      },
    );
    return () => {
      active = false;
    };
  }, [api]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = command.trim();
    if (!normalized || running) return;
    setRunning(true);
    setError("");
    try {
      const startedAt = new Date().toISOString();
      const result = await api.submitCommand(
        normalized,
        conversation?.conversation_id,
      );
      const message: AgentConversationMessage = {
        message_id: result.message_id,
        request_id: result.request_id,
        channel: "console",
        command: normalized,
        status: "completed",
        response: result,
        created_at: startedAt,
        completed_at: new Date().toISOString(),
      };
      setConversation((current) => ({
        conversation_id:
          result.conversation_id ?? current?.conversation_id ?? "",
        status: "active",
        messages: [...(current?.messages ?? []), message],
      }));
      setCommand("");
    } catch {
      setError("指令服务暂时不可用，请稍后重试。");
    } finally {
      setRunning(false);
    }
  }

  async function startNewConversation() {
    if (creating || running) return;
    setCreating(true);
    setError("");
    try {
      const created = await api.createConversation();
      setConversation({
        conversation_id: created.conversation_id,
        status: created.status,
        messages: [],
      });
      setCommand("");
    } catch {
      setError("新会话创建失败，当前对话仍然保留。");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="page agent-page">
      <PageHeader
        title="家庭指令中心"
        description="自然地说出需求，也可以继续引用上一轮的设备和结果。每次控制前仍会读取家庭中的实时状态。"
        actions={<AgentHealth health={health} unavailable={healthUnavailable} />}
      />

      <section className="conversation-surface" aria-label="家庭对话">
        <header className="conversation-toolbar">
          <div>
            <span className="home-pulse" aria-hidden="true" />
            <div>
              <strong>当前会话</strong>
              <span>上下文持续保留，设备状态实时校验</span>
            </div>
          </div>
          <button
            className="button button--secondary"
            type="button"
            disabled={creating || running}
            onClick={startNewConversation}
          >
            {creating ? "创建中…" : "新建会话"}
          </button>
        </header>

        <ConversationTimeline
          messages={conversation?.messages ?? []}
          loading={conversationLoading}
        />
      </section>

      <CommandWorkbench
        command={command}
        running={running}
        onCommandChange={setCommand}
        onSubmit={submit}
      />

      {error ? <p className="conversation-error" role="alert">{error}</p> : null}
    </div>
  );
}

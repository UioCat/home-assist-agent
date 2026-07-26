import { useEffect, useState } from "react";

import { getHealth, submitCommand } from "./api";
import CommandWorkbench from "./components/CommandWorkbench";
import ExecutionRail from "./components/ExecutionRail";
import HealthStatus from "./components/HealthStatus";
import "./styles.css";


export default function App() {
  const [health, setHealth] = useState(null);
  const [command, setCommand] = useState("");
  const [reasoning, setReasoning] = useState("medium");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [isRunning, setIsRunning] = useState(false);

  useEffect(() => {
    let active = true;
    getHealth()
      .then((payload) => {
        if (active) setHealth(payload);
      })
      .catch(() => {
        if (active) setHealth({ unavailable: true });
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleSubmit(event) {
    event.preventDefault();
    const normalized = command.trim();
    if (!normalized || isRunning) return;

    setIsRunning(true);
    setError("");
    setResult(null);
    try {
      const payload = await submitCommand({
        command: normalized,
        reasoning,
      });
      setResult(payload);
    } catch {
      setError("无法连接本地服务，请确认 Python 后端正在运行。");
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Home Assist Agent 首页">
          <span className="brand-mark" aria-hidden="true">
            HA
          </span>
          <span>
            <strong>Home Assist</strong>
            <small>LOCAL AGENT</small>
          </span>
        </a>
        <HealthStatus health={health} />
      </header>

      <main className="workspace">
        <section className="intro" aria-labelledby="page-title">
          <p className="eyebrow">LOCAL COMMAND ROUTER</p>
          <h1 id="page-title">家庭指令中心</h1>
          <p>
            输入一句话。系统会先判断它是直接控制、间接控制还是普通请求，
            再交给 Home Assistant MCP 或本地 Codex。
          </p>
        </section>

        <CommandWorkbench
          command={command}
          reasoning={reasoning}
          isRunning={isRunning}
          onCommandChange={setCommand}
          onReasoningChange={setReasoning}
          onSubmit={handleSubmit}
        />

        <section className="result-region" aria-label="指令执行结果">
          {error ? (
            <div className="result-card result-card--error" role="alert">
              <p className="result-kicker">LOCAL SERVICE ERROR</p>
              <h2>指令未发送</h2>
              <p>{error}</p>
            </div>
          ) : result ? (
            <ExecutionRail result={result} />
          ) : (
            <div className="result-empty">
              <span className="empty-index">01—04</span>
              <div>
                <h2>等待第一条指令</h2>
                <p>执行后，这里会显示分类、分发、工具调用和真实回执。</p>
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

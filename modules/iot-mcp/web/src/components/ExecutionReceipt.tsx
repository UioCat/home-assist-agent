import type { AgentCommandResult } from "../api/agent";

const categoryLabels: Record<string, string> = {
  direct_iot: "直接控制",
  indirect_iot: "间接控制",
  other: "普通请求",
};

const routeLabels: Record<string, string> = {
  home_assistant_mcp: "HA MCP",
  codex: "LOCAL CODEX",
};

export function ExecutionReceipt({ result }: { result: AgentCommandResult }) {
  const alert = result.status === "blocked" || result.status === "error";
  return (
    <article
      className={`execution-receipt execution-receipt--${result.status}`}
      role={alert ? "alert" : undefined}
      aria-live={alert ? undefined : "polite"}
    >
      <header className="execution-receipt__header">
        <div>
          <p className="eyebrow">EXECUTION RECEIPT</p>
          <h2>{categoryLabels[result.category] ?? result.category}</h2>
        </div>
        <div className="execution-receipt__meta">
          <span>{result.status}</span>
          <code>{result.elapsed_ms} ms</code>
        </div>
      </header>

      <ol className="agent-execution-rail" aria-label="执行轨道">
        {result.trace.map((step, index) => (
          <li key={`${step.stage}-${index}`}>
            <span className="mono">{String(index + 1).padStart(2, "0")}</span>
            <i aria-hidden="true" />
            <div>
              <small className="mono">{step.stage}</small>
              <strong>{step.summary}</strong>
            </div>
          </li>
        ))}
      </ol>

      <div className="execution-response">
        <code>{routeLabels[result.route] ?? result.route}</code>
        <p>{result.message}</p>
        {result.error_code ? <code className="error-code">{result.error_code}</code> : null}
      </div>

      {result.tool_call ? (
        <details className="execution-tool">
          <summary><span>工具回执</span><code>{result.tool_call.name}</code></summary>
          <dl>
            <div><dt>参数</dt><dd><pre>{JSON.stringify(result.tool_call.arguments, null, 2)}</pre></dd></div>
            <div><dt>返回</dt><dd>{result.tool_call.result}</dd></div>
          </dl>
        </details>
      ) : null}
    </article>
  );
}

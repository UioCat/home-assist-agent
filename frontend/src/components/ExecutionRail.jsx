const categoryLabels = {
  direct_iot: "直接控制",
  indirect_iot: "间接控制",
  other: "普通请求",
};

const routeLabels = {
  home_assistant_mcp: "HA MCP",
  codex: "LOCAL CODEX",
};

export default function ExecutionRail({ result }) {
  const isAlert = result.status === "blocked" || result.status === "error";

  return (
    <article
      className={`result-card result-card--${result.status}`}
      role={isAlert ? "alert" : undefined}
      aria-live={isAlert ? undefined : "polite"}
    >
      <header className="result-header">
        <div>
          <p className="result-kicker">EXECUTION RECEIPT</p>
          <h2>{categoryLabels[result.category] || result.category}</h2>
        </div>
        <div className="result-meta">
          <span className={`status-badge status-badge--${result.status}`}>
            {result.status}
          </span>
          <span>{result.elapsed_ms} ms</span>
        </div>
      </header>

      <ol className="execution-rail" aria-label="执行轨道">
        {result.trace.map((step, index) => (
          <li
            className={`rail-step rail-step--${step.status}`}
            key={`${step.stage}-${index}`}
          >
            <span className="rail-index">
              {String(index + 1).padStart(2, "0")}
            </span>
            <span className="rail-node" aria-hidden="true" />
            <div>
              <small>{step.stage}</small>
              <strong>{step.summary}</strong>
            </div>
          </li>
        ))}
      </ol>

      <div className="response-block">
        <span className="response-route">
          {routeLabels[result.route] || result.route}
        </span>
        <p>{result.message}</p>
        {result.error_code ? (
          <code className="error-code">{result.error_code}</code>
        ) : null}
      </div>

      {result.tool_call ? (
        <details className="tool-details">
          <summary>
            <span>工具回执</span>
            <code>{result.tool_call.name}</code>
          </summary>
          <dl>
            <div>
              <dt>参数</dt>
              <dd>
                <pre>
                  {JSON.stringify(result.tool_call.arguments, null, 2)}
                </pre>
              </dd>
            </div>
            <div>
              <dt>返回</dt>
              <dd>{result.tool_call.result}</dd>
            </div>
          </dl>
        </details>
      ) : null}
    </article>
  );
}

const routingPolicy = [
  { stage: "意图路由", level: "LOW" },
  { stage: "设备规划", level: "MEDIUM" },
  { stage: "普通回答", level: "HIGH" },
];

const examples = ["打开客厅灯", "客厅太暗了", "介绍一下你能做什么"];

export default function CommandWorkbench({
  command,
  isRunning,
  onCommandChange,
  onSubmit,
}) {
  return (
    <section className="command-panel" aria-label="指令工作台">
      <div className="panel-heading">
        <div>
          <span className="panel-number">01</span>
          <p className="panel-label">输入家庭指令</p>
        </div>
        <span className="local-note">仅在本机处理</span>
      </div>

      <form onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="command-input">
          家庭指令
        </label>
        <textarea
          id="command-input"
          value={command}
          maxLength={1000}
          rows={4}
          placeholder="例如：把客厅灯调到 30%"
          onChange={(event) => onCommandChange(event.target.value)}
        />

        <div className="command-controls">
          <div className="routing-policy" aria-label="固定推理策略">
            <span className="routing-policy-label">固定推理策略</span>
            <ol>
              {routingPolicy.map((item) => (
                <li key={item.stage}>
                  <span>{item.stage}</span>
                  <code>{item.level}</code>
                </li>
              ))}
            </ol>
          </div>

          <button
            className="submit-button"
            type="submit"
            disabled={isRunning || !command.trim()}
          >
            {isRunning ? "处理中…" : "执行指令"}
            <span aria-hidden="true">↗</span>
          </button>
        </div>
      </form>

      <div className="examples" aria-label="示例指令">
        <span>试一条</span>
        {examples.map((example) => (
          <button
            type="button"
            key={example}
            onClick={() => onCommandChange(example)}
          >
            {example}
          </button>
        ))}
      </div>
    </section>
  );
}

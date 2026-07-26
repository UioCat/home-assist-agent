const reasoningOptions = [
  { value: "low", label: "低", hint: "更快" },
  { value: "medium", label: "中", hint: "均衡" },
  { value: "high", label: "高", hint: "深入" },
];

const examples = ["打开客厅灯", "客厅太暗了", "介绍一下你能做什么"];

export default function CommandWorkbench({
  command,
  reasoning,
  isRunning,
  onCommandChange,
  onReasoningChange,
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
          <fieldset className="reasoning-control">
            <legend>思考等级</legend>
            <div className="reasoning-options">
              {reasoningOptions.map((option) => (
                <label key={option.value}>
                  <input
                    type="radio"
                    name="reasoning"
                    value={option.value}
                    aria-label={option.label}
                    checked={reasoning === option.value}
                    onChange={() => onReasoningChange(option.value)}
                  />
                  <span>
                    <strong>{option.label}</strong>
                    <small>{option.hint}</small>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

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

const examples = ["打开客厅灯", "客厅太暗了", "介绍一下你能做什么"];

export function CommandWorkbench({
  command,
  running,
  onCommandChange,
  onSubmit,
}: {
  command: string;
  running: boolean;
  onCommandChange(value: string): void;
  onSubmit(event: React.FormEvent<HTMLFormElement>): void;
}) {
  return (
    <section className="command-workbench" aria-label="指令工作台">
      <header className="command-workbench__header">
        <div>
          <span className="mono">01</span>
          <div>
            <h2>继续对话</h2>
          </div>
        </div>
      </header>

      <form onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="command-input">家庭指令</label>
        <textarea
          id="command-input"
          value={command}
          maxLength={1000}
          rows={3}
          placeholder="输入指令，或接着上一句继续说…"
          onChange={(event) => onCommandChange(event.target.value)}
        />
        <div className="command-workbench__controls">
          <span className="command-context-note">会记住当前会话中的设备与指代</span>
          <button
            className="button button--primary command-submit"
            type="submit"
            disabled={running || !command.trim()}
          >
            {running ? "处理中…" : "发送"}
            <span aria-hidden="true">↗</span>
          </button>
        </div>
      </form>

      <div className="command-examples" aria-label="示例指令">
        <span>试一条</span>
        {examples.map((example) => (
          <button type="button" key={example} onClick={() => onCommandChange(example)}>
            {example}
          </button>
        ))}
      </div>
    </section>
  );
}

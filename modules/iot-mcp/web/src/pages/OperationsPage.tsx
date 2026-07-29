import { useState } from "react";

import { useApi } from "../api/context";
import type { ConfirmationItem } from "../api/types";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { SignalTrail } from "../components/SignalTrail";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime, humanize } from "../components/format";

export function OperationsPage() {
  const api = useApi();
  const [decisionResult, setDecisionResult] = useState("");
  const [busyId, setBusyId] = useState("");
  const query = useQuery(async () => {
    const [operations, confirmations] = await Promise.all([
      api.listOperations(),
      api.listConfirmations(),
    ]);
    return { operations, confirmations };
  }, [api]);

  async function decide(item: ConfirmationItem, decision: "approve" | "reject") {
    setBusyId(item.confirmation.confirmation_id);
    setDecisionResult("");
    try {
      const operation = await api.decideConfirmation(
        item.confirmation.confirmation_id,
        decision,
        item.confirmation.action_hash,
      );
      setDecisionResult(`决定已提交：${decision === "approve" ? "批准" : "拒绝"} · ${operation.status}`);
      query.reload();
    } catch (error) {
      setDecisionResult(`提交失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusyId("");
    }
  }

  const pending = query.data?.confirmations.filter((item) => item.confirmation.decision === "pending") ?? [];

  return (
    <div className="page">
      <PageHeader
        eyebrow="AUDIT & DECISION"
        title="操作与确认"
        description="确认只处理自动来源的高风险动作；人工设备直控不会来到这里等待二次确认。"
      />
      {query.loading ? <PageState state="loading" label="正在读取操作账本" /> : null}
      {query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
      {query.data ? (
        <>
          <section className="decision-zone" aria-labelledby="pending-title">
            <div className="decision-zone__header">
              <div><p className="eyebrow">AUTONOMOUS HIGH RISK</p><h2 id="pending-title">待确认决定</h2></div>
              <span className="decision-count">{pending.length} 项需要决定</span>
            </div>
            <p className="decision-zone__guidance">先核对来源、目标、动作哈希、绑定修订和过期时间，再做明确决定。</p>
            <p className="inline-result" role="status" aria-live="polite">{decisionResult}</p>
            {!pending.length ? <PageState state="empty" label="当前没有待确认操作" /> : (
              <div className="confirmation-list">
                {pending.map((item) => (
                  <article className="confirmation-row" key={item.confirmation.confirmation_id}>
                    <div className="confirmation-row__source">
                      <span className="source-label">自动任务</span>
                      <strong>{item.operation?.source_label ?? "未知来源"}</strong>
                      <span className="mono">{item.confirmation.confirmation_id}</span>
                    </div>
                    <div className="confirmation-row__action">
                      <strong>{item.operation?.action_summary ?? "无动作摘要"}</strong>
                      {item.operation?.sensitive_values_redacted ? (
                        <small className="redaction-note">敏感值已隐藏</small>
                      ) : null}
                      <dl>
                        <div><dt>目标</dt><dd className="mono">{item.confirmation.target}</dd></div>
                        <div><dt>Provider</dt><dd className="mono">{item.confirmation.provider_id ?? "unbound"}</dd></div>
                        <div><dt>过期</dt><dd className="mono">{formatDateTime(item.confirmation.expires_at)}</dd></div>
                        <div><dt>绑定</dt><dd>r{item.confirmation.binding_revision}</dd></div>
                      </dl>
                    </div>
                    <div className="confirmation-row__risk"><StatusBadge value="high" /><span className="mono" title={item.confirmation.action_hash}>{item.confirmation.action_hash}</span></div>
                    <div className="confirmation-row__actions">
                      <button className="button button--danger" disabled={busyId === item.confirmation.confirmation_id} onClick={() => decide(item, "reject")}>拒绝此操作</button>
                      <button className="button button--primary" disabled={busyId === item.confirmation.confirmation_id} onClick={() => decide(item, "approve")}>批准此操作</button>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="work-surface">
            <div className="section-heading"><div><p className="eyebrow">LEDGER</p><h2>完整操作账本</h2></div></div>
            {!query.data.operations.length ? <PageState state="empty" label="尚无操作记录" /> : (
              <div className="operation-list">
                {query.data.operations.map((operation) => (
                  <details className="ledger-row" key={operation.operation_id}>
                    <summary>
                      <SignalTrail compact status={operation.status} timestamp={operation.updated_at} provider={operation.provider_id ?? "unbound"} />
                      <span className="ledger-row__action"><strong>{operation.action_summary}</strong><small>{operation.source_label} · {humanize(operation.source_category)}</small></span>
                      <StatusBadge value={operation.status} />
                    </summary>
                    <div className="ledger-row__detail">
                      <div><span>Operation ID</span><code>{operation.operation_id}</code></div>
                      <div><span>目标</span><code>{operation.target}</code></div>
                      <div><span>绑定修订</span><code>{operation.binding_revision ? `r${operation.binding_revision}` : "unbound"}</code></div>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}

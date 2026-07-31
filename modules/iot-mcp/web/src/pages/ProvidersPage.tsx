import { useState } from "react";

import { useApi } from "../api/context";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";

export function ProvidersPage() {
  const api = useApi();
  const query = useQuery(() => api.listProviders(), [api]);
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState("");

  async function sync(providerId: string) {
    setBusy(providerId);
    setResult("");
    try {
      const value = await api.syncProvider(providerId);
      setResult(`${providerId} 同步完成：发现 ${value.discovered}，更新 ${value.upserted}，失联 ${value.missing}，快照 ${value.snapshots}`);
      query.reload();
    } catch (error) {
      setResult(`同步失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="page">
      <PageHeader eyebrow="PROVIDER ROUTES" title="Provider" description="设备实时状态的事实源；本地快照只表达最近一次 observed_at。" actions={<button className="button button--secondary" onClick={query.reload}>重新诊断</button>} />
      <p className="inline-result" aria-live="polite">{result}</p>
      {query.loading ? <PageState state="loading" label="正在诊断 Provider" /> : null}
      {query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
      {query.data?.length === 0 ? <PageState state="empty" label="没有已注册 Provider" /> : null}
      {query.data?.length ? (
        <section className="provider-console">
          {query.data.map((provider) => (
            <article className="work-surface provider-console__row" key={provider.provider_id}>
              <div className="provider-console__identity"><span className="provider-glyph" aria-hidden="true">P</span><div><strong className="mono">{provider.provider_id}</strong><span>{provider.provider_type}</span></div></div>
              <div className="provider-console__status"><span>连接状态</span><StatusBadge value={provider.status} /></div>
              <div className="provider-console__detail"><span>诊断</span><strong>{provider.detail ?? "连接与实时读取正常"}</strong></div>
              <button className="button button--secondary" disabled={busy === provider.provider_id} onClick={() => sync(provider.provider_id)}>{busy === provider.provider_id ? "同步中…" : "手动同步"}</button>
            </article>
          ))}
        </section>
      ) : null}
      <section className="work-surface diagnostic-note">
        <p className="eyebrow">STATE SEMANTICS</p>
        <dl className="definition-grid">
          <div><dt>healthy</dt><dd>Provider 健康且最近同步成功</dd></div>
          <div><dt>degraded</dt><dd>部分能力或实时订阅不可用</dd></div>
          <div><dt>unavailable</dt><dd>控制被阻断，查询明确返回不可用</dd></div>
        </dl>
      </section>
    </div>
  );
}

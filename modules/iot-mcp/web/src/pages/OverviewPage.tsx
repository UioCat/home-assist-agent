import { Link } from "react-router-dom";

import { useApi } from "../api/context";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { SignalTrail } from "../components/SignalTrail";
import { StatusBadge } from "../components/StatusBadge";
import { actionSummary, formatDateTime } from "../components/format";
import { consolePath } from "../components/routing";

export function OverviewPage() {
  const api = useApi();
  const query = useQuery(
    async () => {
      const [devices, providers, operations, confirmations, events] = await Promise.all([
        api.listDevices(),
        api.listProviders(),
        api.listOperations(),
        api.listConfirmations("pending"),
        api.listEvents(),
      ]);
      return { devices, providers, operations, confirmations, events };
    },
    [api],
  );

  if (query.loading) return <PageFrame><PageState state="loading" label="正在汇总家庭设备信号" /></PageFrame>;
  if (query.error || !query.data) return <PageFrame><ErrorState error={query.error} onRetry={query.reload} /></PageFrame>;

  const stale = query.data.devices.filter((device) => device.status !== "active");
  const failed = query.data.operations.filter((operation) =>
    ["failed", "unknown", "rejected"].includes(operation.status),
  );
  const priorityDevices = [...stale, ...query.data.devices.filter((device) => device.risk_level === "high")]
    .filter((device, index, all) => all.findIndex((candidate) => candidate.device_id === device.device_id) === index)
    .slice(0, 5);

  return (
    <div className="page">
      <PageHeader
        eyebrow="HOME SIGNAL / OVERVIEW"
        title="先处理失联与待确认"
        description="新鲜度、来源和下一步动作集中在同一条设备信号轨迹上。"
        actions={<button className="button button--secondary" onClick={query.reload}>刷新信号</button>}
      />
      <section className="overview-strip" aria-label="家庭控制平面摘要">
        <div className="overview-strip__lead">
          <span className="eyebrow">当前判断</span>
          <strong>{stale.length || failed.length ? "控制平面需要关注" : "家庭设备运行平稳"}</strong>
          <span>{stale.length} 台失联 · {query.data.confirmations.length} 项待确认 · {failed.length} 项失败</span>
        </div>
        <dl className="inline-facts">
          <div><dt>设备</dt><dd>{query.data.devices.length}</dd></div>
          <div><dt>Provider</dt><dd>{query.data.providers.length}</dd></div>
          <div><dt>最近事件</dt><dd>{query.data.events.length}</dd></div>
        </dl>
      </section>

      <div className="dashboard-grid">
        <section className="work-surface dashboard-grid__main">
          <div className="section-heading">
            <div><p className="eyebrow">DEVICE SIGNAL TRAIL</p><h2>需要判断的设备</h2></div>
            <Link className="text-link" to={consolePath("/devices")}>查看全部设备</Link>
          </div>
          {priorityDevices.length === 0 ? (
            <PageState state="empty" label="没有失联或高风险设备" />
          ) : (
            <div className="signal-list">
              {priorityDevices.map((device) => (
                <Link className="signal-row" key={device.device_id} to={consolePath(`/devices/${device.device_id}`)}>
                  <div><strong>{device.display_name}</strong><span>{device.area ?? "未分区"} · <StatusBadge value={device.risk_level} /></span></div>
                  <SignalTrail
                    status={device.status}
                    timestamp={device.updated_at}
                    provider={device.provider_id}
                  />
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="work-surface">
          <div className="section-heading"><div><p className="eyebrow">PROVIDER</p><h2>连接健康</h2></div></div>
          <div className="provider-stack">
            {query.data.providers.map((provider) => (
              <div className="provider-line" key={provider.provider_id}>
                <div><strong className="mono">{provider.provider_id}</strong><span>{provider.detail ?? provider.provider_type}</span></div>
                <StatusBadge value={provider.status} />
              </div>
            ))}
          </div>
        </section>

        <section className="work-surface dashboard-grid__wide">
          <div className="section-heading">
            <div><p className="eyebrow">OPERATIONS</p><h2>最近动作与确认</h2></div>
            <Link className="text-link" to={consolePath("/operations")}>进入操作台</Link>
          </div>
          {query.data.operations.length === 0 ? <PageState state="empty" label="暂无操作记录" /> : (
            <div className="operation-list">
              {query.data.operations.slice(0, 4).map((operation) => (
                <div className="operation-row" key={operation.operation_id}>
                  <SignalTrail
                    compact
                    status={operation.status}
                    timestamp={operation.updated_at}
                    provider={operation.provider_id ?? "unbound"}
                  />
                  <div className="operation-row__summary">
                    <strong>{actionSummary(operation.action)}</strong>
                    <span>{operation.initiator} · {formatDateTime(operation.created_at)}</span>
                  </div>
                  <StatusBadge value={operation.status} />
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function PageFrame({ children }: { children: React.ReactNode }) {
  return <div className="page"><PageHeader eyebrow="HOME SIGNAL / OVERVIEW" title="家庭设备概览" description="正在建立控制平面上下文。" />{children}</div>;
}

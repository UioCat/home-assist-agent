import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useApi } from "../api/context";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { SignalTrail } from "../components/SignalTrail";
import { StatusBadge } from "../components/StatusBadge";
import { consolePath } from "../components/routing";

export function DevicesPage() {
  const api = useApi();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const query = useQuery(() => api.listDevices(), [api]);
  const filtered = useMemo(
    () =>
      (query.data ?? []).filter(
        (device) =>
          (status === "all" || device.status === status) &&
          `${device.display_name} ${device.area ?? ""} ${device.provider_id}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      ),
    [query.data, search, status],
  );

  return (
    <div className="page">
      <PageHeader
        eyebrow="DEVICE INVENTORY"
        title="设备实例"
        description="每一行都是实际控制目标；状态、新鲜度和 Provider 路由同时可见。"
      />
      <div className="filter-bar" role="search">
        <label><span>搜索设备</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称、区域或 Provider" /></label>
        <label><span>运行状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">全部</option><option value="active">正常</option><option value="missing">失联</option></select></label>
        <span className="filter-bar__count">{filtered.length} / {query.data?.length ?? 0} 台</span>
      </div>
      {query.loading ? <PageState state="loading" label="正在读取设备实例" /> : null}
      {query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
      {!query.loading && !query.error && filtered.length === 0 ? <PageState state="empty" label="没有符合筛选条件的设备" /> : null}
      {filtered.length ? (
        <section className="work-surface table-surface">
          <div className="device-table" role="table" aria-label="设备实例">
            <div className="table-row table-row--header" role="row">
              <span role="columnheader">设备 / 区域</span><span role="columnheader">信号轨迹</span><span role="columnheader">风险</span><span role="columnheader">操作</span>
            </div>
            {filtered.map((device) => (
              <div className="table-row" role="row" key={device.device_id}>
                <span role="cell"><strong>{device.display_name}</strong><span className="mono">{device.device_id}</span><small>{device.area ?? "未分区"}</small></span>
                <span role="cell"><SignalTrail status={device.status} timestamp={device.updated_at} provider={device.provider_id} /></span>
                <span role="cell"><StatusBadge value={device.risk_level} /></span>
                <span role="cell"><Link className="button button--secondary" to={consolePath(`/devices/${device.device_id}`)}>打开控制台</Link></span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

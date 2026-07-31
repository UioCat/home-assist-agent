import { useMemo, useState } from "react";

import { useApi } from "../api/context";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../components/format";

export function EventsPage() {
  const api = useApi();
  const [deviceId, setDeviceId] = useState("all");
  const [search, setSearch] = useState("");
  const query = useQuery(async () => {
    const [events, devices] = await Promise.all([api.listEvents(), api.listDevices()]);
    return { events, devices };
  }, [api]);
  const filtered = useMemo(
    () => (query.data?.events ?? []).filter((event) =>
      (deviceId === "all" || event.device_id === deviceId) &&
      `${event.identifier} ${event.type} ${event.source}`.toLowerCase().includes(search.toLowerCase()),
    ),
    [deviceId, query.data, search],
  );

  return (
    <div className="page">
      <PageHeader eyebrow="EVENT FEED" title="设备事件" description="只展示已持久化的 TSL 事件、属性变化与 Provider 诊断，不补造缺失信号。" />
      <div className="filter-bar" role="search">
        <label><span>搜索事件</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="identifier、类型或来源" /></label>
        <label><span>设备</span><select value={deviceId} onChange={(event) => setDeviceId(event.target.value)}><option value="all">全部设备</option>{query.data?.devices.map((device) => <option value={device.device_id} key={device.device_id}>{device.display_name}</option>)}</select></label>
        <span className="filter-bar__count">{filtered.length} 条</span>
      </div>
      {query.loading ? <PageState state="loading" label="正在读取设备事件" /> : null}
      {query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
      {!query.loading && !query.error && !filtered.length ? <PageState state="empty" label="没有符合条件的设备事件" /> : null}
      {filtered.length ? (
        <section className="work-surface event-feed">
          {filtered.map((event) => (
            <article className="event-row" key={event.event_id}>
              <div className="event-row__time"><time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time><span className="mono">{event.source}</span></div>
              <span className="event-row__line" aria-hidden="true"><i /></span>
              <div className="event-row__content"><strong>{event.identifier}</strong><span>{event.type}</span><code>{JSON.stringify(event.output_data)}</code></div>
              <StatusBadge value={event.type === "warning" ? "degraded" : "active"} />
            </article>
          ))}
        </section>
      ) : null}
    </div>
  );
}

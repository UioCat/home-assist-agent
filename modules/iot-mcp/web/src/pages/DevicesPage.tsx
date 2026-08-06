import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import type { DeviceCard as DeviceCardData } from "../api/types";
import { useApi } from "../api/context";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { formatDateTime, formatValue, humanize } from "../components/format";
import { consolePath } from "../components/routing";

const DEVICE_TYPE_ORDER = [
  "light",
  "outlet",
  "climate",
  "heater",
  "humidifier",
  "lock",
  "appliance",
  "switch",
  "other",
] as const;

function deviceStateLabel(card: DeviceCardData): string {
  if (card.availability === "offline") return "离线";
  if (card.availability === "unknown") return "状态未知";
  const control = card.primary_control;
  if (!control) return card.freshness === "unknown" ? "状态未知" : "状态可用";
  if (control.identifier === "LockState") {
    return control.current_value === "LOCK" ? "已上锁" : "已解锁";
  }
  return formatValue(control.current_value);
}

function controlTarget(card: DeviceCardData): unknown {
  const control = card.primary_control;
  if (!control) return undefined;
  if (control.identifier === "LockState") {
    return control.current_value === "LOCK" ? "UNLOCK" : "LOCK";
  }
  if (typeof control.current_value === "boolean") return !control.current_value;
  return undefined;
}

function controlAction(card: DeviceCardData): string {
  const control = card.primary_control;
  if (control?.identifier === "LockState") {
    return control.current_value === "LOCK" ? "解锁" : "上锁";
  }
  return control?.current_value === true ? "关闭" : "开启";
}

function DeviceCard({
  card,
  onWrite,
}: {
  card: DeviceCardData;
  onWrite: (identifier: string, value: unknown) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const active = card.primary_control?.current_value === true
    || card.primary_control?.current_value === "UNLOCK";
  const available = card.availability === "online";
  const target = controlTarget(card);

  async function write() {
    if (!card.primary_control || target === undefined) return;
    setBusy(true);
    setResult("");
    try {
      await onWrite(card.primary_control.identifier, target);
      setResult("操作已发送");
    } catch (error) {
      setResult(`操作失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      aria-label={card.display_name}
      className={`device-card${active && available ? " device-card--active" : ""}${card.availability === "offline" ? " device-card--offline" : ""}${card.availability === "unknown" ? " device-card--unknown" : ""}`}
    >
      <Link
        aria-label={`打开${card.display_name}详情`}
        className="device-card__link"
        to={consolePath(`/devices/${card.device_id}`)}
      >
        <span className="device-card__icon" aria-hidden="true">
          {card.primary_control?.identifier === "LockState" ? "⌂" : "●"}
        </span>
        <span className="device-card__copy">
          <strong>{card.display_name}</strong>
          <span>{deviceStateLabel(card)}</span>
        </span>
        <span className="device-card__provider mono">{card.provider_id}</span>
        {card.secondary_status.length ? (
          <span className="device-card__facts">
            {card.secondary_status.map((status) => (
              <span key={status.identifier}>
                {status.name} {formatValue(status.value)}{status.unit ?? ""}
              </span>
            ))}
          </span>
        ) : null}
        <span className="device-card__freshness">
          {humanize(card.freshness)} · {formatDateTime(card.observed_at)}
        </span>
      </Link>
      {card.primary_control && target !== undefined ? (
        <button
          aria-label={available
            ? `${controlAction(card)}${card.display_name}`
            : card.availability === "offline"
              ? `${card.display_name}离线，无法控制`
              : `${card.display_name}状态未知，无法控制`}
          className="device-card__control"
          disabled={busy || !available}
          onClick={write}
          type="button"
        >
          <span aria-hidden="true" />
        </button>
      ) : null}
      <span className="device-card__result" aria-live="polite">{result}</span>
    </article>
  );
}

export function DevicesPage() {
  const api = useApi();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [deviceType, setDeviceType] = useState("all");
  const query = useQuery(() => api.listDeviceCards(), [api]);
  const typeFilters = useMemo(() => {
    const counts = new Map<string, { label: string; count: number }>();
    for (const device of query.data ?? []) {
      const current = counts.get(device.device_type);
      counts.set(device.device_type, {
        label: device.device_type_label,
        count: (current?.count ?? 0) + 1,
      });
    }
    return DEVICE_TYPE_ORDER.flatMap((type) => {
      const item = counts.get(type);
      return item ? [{ type, ...item }] : [];
    });
  }, [query.data]);
  useEffect(() => {
    if (
      deviceType !== "all"
      && !typeFilters.some((item) => item.type === deviceType)
    ) {
      setDeviceType("all");
    }
  }, [deviceType, typeFilters]);
  const filtered = useMemo(
    () =>
      (query.data ?? []).filter(
        (device) =>
          (status === "all" || device.availability === status)
          && (deviceType === "all" || device.device_type === deviceType)
          && `${device.display_name} ${device.area ?? ""} ${device.provider_id}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      ),
    [deviceType, query.data, search, status],
  );
  const groups = useMemo(() => {
    const byArea = new Map<string, DeviceCardData[]>();
    for (const device of filtered) {
      const area = device.area ?? "未分区";
      byArea.set(area, [...(byArea.get(area) ?? []), device]);
    }
    return [...byArea.entries()]
      .sort(([left], [right]) => {
        if (left === "未分区") return 1;
        if (right === "未分区") return -1;
        return left.localeCompare(right, "zh-CN");
      })
      .map(([area, devices]) => [
        area,
        devices.sort((left, right) =>
          left.display_name.localeCompare(right.display_name, "zh-CN")),
      ] as const);
  }, [filtered]);

  return (
    <div className="page">
      <PageHeader
        eyebrow="DEVICE INVENTORY"
        title="设备实例"
        description="按区域查看来自各个 Provider 的真实设备、状态与可用操作。"
      />
      <div className="filter-bar" role="search">
        <label><span>搜索设备</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称、区域或 Provider" /></label>
        <label><span>运行状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">全部</option><option value="online">在线</option><option value="offline">离线</option><option value="unknown">状态未知</option></select></label>
        <span className="filter-bar__count">{filtered.length} / {query.data?.length ?? 0} 台</span>
      </div>
      {!query.loading && !query.error && (query.data?.length ?? 0) > 0 ? (
        <nav aria-label="设备类型筛选" className="device-type-rail">
          <button
            aria-pressed={deviceType === "all"}
            className="device-type-rail__button"
            onClick={() => setDeviceType("all")}
            type="button"
          >
            <span>全部</span><strong>{query.data?.length ?? 0}</strong>
          </button>
          {typeFilters.map((item) => (
            <button
              aria-pressed={deviceType === item.type}
              className="device-type-rail__button"
              key={item.type}
              onClick={() => setDeviceType(item.type)}
              type="button"
            >
              <span>{item.label}</span><strong>{item.count}</strong>
            </button>
          ))}
        </nav>
      ) : null}
      {query.loading ? <PageState state="loading" label="正在读取设备实例" /> : null}
      {query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
      {!query.loading && !query.error && filtered.length === 0 ? <PageState state="empty" label="没有符合筛选条件的设备" /> : null}
      {groups.map(([area, devices]) => (
        <section className="device-area" key={area} aria-labelledby={`device-area-${area}`}>
          <div className="device-area__heading">
            <h2 id={`device-area-${area}`}>{area}</h2>
            <span>{devices.length} 台</span>
          </div>
          <div className="device-card-grid">
            {devices.map((device) => (
              <DeviceCard
                card={device}
                key={device.device_id}
                onWrite={async (identifier, value) => {
                  await api.writeProperties(device.device_id, { [identifier]: value });
                  query.reload();
                }}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

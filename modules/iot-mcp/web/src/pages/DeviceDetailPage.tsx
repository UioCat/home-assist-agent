import { Link, useParams } from "react-router-dom";

import { useApi } from "../api/context";
import { useQuery } from "../api/useQuery";
import { PropertyControl, ServiceControl } from "../components/CapabilityControls";
import { ErrorState, PageState } from "../components/PageState";
import { SignalTrail } from "../components/SignalTrail";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime, formatValue } from "../components/format";
import { consolePath } from "../components/routing";

export function DeviceDetailPage() {
  const { deviceId = "" } = useParams();
  const api = useApi();
  const query = useQuery(async () => {
    const [detail, state, operations, events] = await Promise.all([
      api.getDevice(deviceId),
      api.getDeviceState(deviceId),
      api.listOperations(),
      api.listEvents(deviceId),
    ]);
    return {
      detail,
      state,
      operations: operations.filter((operation) => operation.device_id === deviceId),
      events,
    };
  }, [api, deviceId]);

  if (query.loading) return <div className="page"><PageState state="loading" label="正在建立设备实时控制上下文" /></div>;
  if (query.error || !query.data) return <div className="page"><ErrorState error={query.error} onRetry={query.reload} /></div>;

  const { detail, state, operations, events } = query.data;
  const model = detail.bound_model;
  const riskFor = (identifier: string) =>
    detail.feature_bindings.find((binding) => binding.identifier === identifier)?.risk_level ??
    detail.device.risk_level;

  return (
    <div className="page">
      <nav className="breadcrumb" aria-label="面包屑"><Link to={consolePath("/devices")}>设备实例</Link><span>/</span><span>{detail.device.display_name}</span></nav>
      <header className="device-header">
        <div>
          <p className="eyebrow mono">{detail.device.device_id}</p>
          <h1>{detail.device.display_name}</h1>
          <p>{detail.device.area ?? "未分区"} · {model ? `${String(model.tsl_json.profile.productKey)} / v${model.version}` : "未绑定物模型"}</p>
        </div>
        <div className="device-header__signal">
          <SignalTrail status={state.freshness} timestamp={state.observed_at} provider={detail.device.provider_id} />
          <StatusBadge value={detail.device.risk_level} />
        </div>
      </header>

      <div className="device-workbench">
        <div className="device-workbench__main">
          <section className="work-surface">
            <div className="section-heading"><div><p className="eyebrow">OBSERVED PROPERTIES</p><h2>实时属性</h2></div><span className="mono section-meta">{formatDateTime(state.observed_at)}</span></div>
            {Object.keys(state.values).length === 0 ? <PageState state="empty" label="Provider 未返回属性" /> : (
              <dl className="property-grid">
                {Object.entries(state.values).map(([identifier, value]) => (
                  <div key={identifier}><dt className="mono">{identifier}</dt><dd>{formatValue(value)}</dd><small>observed_at {formatDateTime(state.observed_at)}</small></div>
                ))}
              </dl>
            )}
          </section>

          <section className="work-surface controls-surface">
            <div className="section-heading">
              <div><p className="eyebrow">HUMAN INTERACTIVE</p><h2>属性直控</h2></div>
              <p className="section-note">人工点击直接发送；结果在行内返回。</p>
            </div>
            {!model ? <PageState state="empty" label="未绑定 TSL，无法生成写入控件" /> : (
              <div className="control-list">
                {model.tsl_json.properties.filter((property) => property.accessMode === "rw").map((property) => (
                  <PropertyControl
                    key={property.identifier}
                    property={property}
                    currentValue={state.values[property.identifier]}
                    risk={riskFor(property.identifier)}
                    onSubmit={(value) => api.writeProperties(deviceId, { [property.identifier]: value })}
                  />
                ))}
              </div>
            )}
          </section>

          <section className="work-surface">
            <div className="section-heading"><div><p className="eyebrow">TSL SERVICES</p><h2>服务调用</h2></div></div>
            {!model?.tsl_json.services.length ? <PageState state="empty" label="此设备没有可调用服务" /> : (
              <div className="service-list">
                {model.tsl_json.services.map((service) => (
                  <ServiceControl key={service.identifier} service={service} risk={riskFor(service.identifier)} onSubmit={(inputs) => api.invokeService(deviceId, service.identifier, inputs)} />
                ))}
              </div>
            )}
          </section>

          <section className="work-surface">
            <div className="section-heading"><div><p className="eyebrow">OPERATION HISTORY</p><h2>设备操作记录</h2></div></div>
            {!operations.length ? <PageState state="empty" label="尚无操作记录" /> : (
              <div className="operation-list">
                {operations.map((operation) => (
                  <div className="operation-row operation-row--device" key={operation.operation_id}>
                    <SignalTrail compact status={operation.status} timestamp={operation.updated_at} provider={operation.provider_id ?? "unbound"} />
                    <div className="operation-row__summary"><strong>{operation.action_summary}</strong><span>{operation.source_label}</span></div>
                    <StatusBadge value={operation.source_category} />
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        <aside className="device-workbench__aside" aria-label="设备上下文">
          <section className="work-surface context-panel">
            <p className="eyebrow">PROVIDER BINDING</p>
            {detail.bindings.map((binding) => (
              <dl className="context-list" key={binding.binding_id}>
                <div><dt>Provider</dt><dd className="mono">{binding.provider_id ?? binding.provider_type}</dd></div>
                <div><dt>外部引用</dt><dd className="mono">{binding.external_device_ref}</dd></div>
                <div><dt>绑定修订</dt><dd>r{binding.binding_revision}</dd></div>
                <div><dt>Binding ID</dt><dd className="mono">{binding.binding_id}</dd></div>
              </dl>
            ))}
          </section>
          <section className="work-surface context-panel">
            <p className="eyebrow">RECENT EVENTS</p>
            {!events.length ? <p className="muted">暂无设备事件</p> : events.slice(0, 4).map((event) => (
              <div className="context-event" key={event.event_id}><strong>{event.identifier}</strong><span>{event.type} · {formatDateTime(event.occurred_at)}</span></div>
            ))}
          </section>
        </aside>
      </div>
    </div>
  );
}

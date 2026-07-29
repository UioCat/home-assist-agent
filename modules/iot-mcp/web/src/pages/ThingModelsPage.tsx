import { useRef, useState } from "react";

import { useApi } from "../api/context";
import type { ThingModelVersion, ThingProduct } from "../api/types";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../components/format";

interface ModelRow {
  product: ThingProduct;
  versions: ThingModelVersion[];
}

export function ThingModelsPage() {
  const api = useApi();
  const [selected, setSelected] = useState<ModelRow | null>(null);
  const [validation, setValidation] = useState("");
  const [validationBusy, setValidationBusy] = useState(false);
  const validationLock = useRef(false);
  const query = useQuery(async () => {
    const products = await api.listThingModels();
    const rows = await Promise.all(
      products.map(async (product) => ({
        product,
        versions: await api.listThingModelVersions(product.product_id),
      })),
    );
    return rows;
  }, [api]);

  const selectRow = (row: ModelRow) => {
    setSelected(row);
    setValidation("");
  };

  async function validate() {
    const version = selected?.versions[0];
    if (!version || validationLock.current) return;
    validationLock.current = true;
    setValidationBusy(true);
    setValidation("正在校验…");
    try {
      await api.validateThingModel(version.model_version_id);
      setValidation(`版本 v${version.version} 通过标准 TSL 校验`);
    } catch (error) {
      setValidation(error instanceof Error ? error.message : "校验失败");
    } finally {
      validationLock.current = false;
      setValidationBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="CONTROL CONTRACT / TSL"
        title="物模型"
        description="产品版本定义属性、服务和事件；Provider 绑定不改变这份控制契约。"
      />
      {query.loading ? <PageState state="loading" label="正在读取产品与版本" /> : null}
      {query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
      {query.data?.length === 0 ? <PageState state="empty" label="尚未导入物模型" /> : null}
      {query.data?.length ? (
        <div className="split-workbench">
          <section className="work-surface list-pane" aria-label="物模型列表">
            <div className="section-heading"><div><p className="eyebrow">PRODUCTS</p><h2>{query.data.length} 个控制契约</h2></div></div>
            {query.data.map((row) => (
              <button
                type="button"
                className={`select-row${selected?.product.product_id === row.product.product_id ? " select-row--active" : ""}`}
                key={row.product.product_id}
                onClick={() => selectRow(row)}
              >
                <span><strong>{row.product.name}</strong><span className="mono">{row.product.product_key}</span></span>
                <span className="select-row__meta">v{row.versions[0]?.version ?? "—"}<StatusBadge value={row.versions[0]?.status ?? "unknown"} /></span>
              </button>
            ))}
          </section>
          <section className="work-surface detail-pane">
            {!selected ? <PageState state="empty" label="选择一个产品查看 TSL" /> : (
              <>
                <div className="section-heading">
                  <div><p className="eyebrow mono">{selected.product.product_key}</p><h2>{selected.product.name}</h2></div>
                  <button className="button button--secondary" disabled={!selected.versions[0] || validationBusy} onClick={validate}>{validationBusy ? "正在校验…" : "校验当前版本"}</button>
                </div>
                {validation ? <p className="inline-result" role="status">{validation}</p> : null}
                {selected.versions.map((version) => (
                  <div className="model-inspector" key={version.model_version_id}>
                    <dl className="definition-grid">
                      <div><dt>版本</dt><dd>v{version.version}</dd></div>
                      <div><dt>状态</dt><dd><StatusBadge value={version.status} /></dd></div>
                      <div><dt>创建时间</dt><dd className="mono">{formatDateTime(version.created_at)}</dd></div>
                      <div><dt>指纹</dt><dd className="mono">{selected.product.capability_fingerprint}</dd></div>
                    </dl>
                    <div className="capability-tabs" aria-label="能力数量">
                      <span><strong>{version.tsl_json.properties.length}</strong> 属性</span>
                      <span><strong>{version.tsl_json.services.length}</strong> 服务</span>
                      <span><strong>{version.tsl_json.events.length}</strong> 事件</span>
                    </div>
                    <details>
                      <summary>查看原始 TSL JSON</summary>
                      <pre>{JSON.stringify(version.tsl_json, null, 2)}</pre>
                    </details>
                  </div>
                ))}
              </>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}

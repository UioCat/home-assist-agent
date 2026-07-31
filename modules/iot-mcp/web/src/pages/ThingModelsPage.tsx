import { useRef, useState } from "react";

import { useApi } from "../api/context";
import type {
  ThingModelVersion,
  ThingProduct,
  TslDocument,
} from "../api/types";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../components/format";

interface ModelRow {
  product: ThingProduct;
  versions: ThingModelVersion[];
}

type ModelAction = "validate" | "publish" | "archive" | "export";

export function ThingModelsPage() {
  const api = useApi();
  const [selectedProductId, setSelectedProductId] = useState<string | null>(
    null,
  );
  const [feedback, setFeedback] = useState("");
  const [actionBusy, setActionBusy] = useState<ModelAction | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importName, setImportName] = useState("");
  const [importDocument, setImportDocument] = useState<TslDocument | null>(
    null,
  );
  const [importFileName, setImportFileName] = useState("");
  const [importError, setImportError] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const actionLock = useRef(false);
  const importLock = useRef(false);
  const query = useQuery(async () => {
    const products = await api.listThingModels();
    return Promise.all(
      products.map(async (product) => ({
        product,
        versions: await api.listThingModelVersions(product.product_id),
      })),
    );
  }, [api]);
  const selected =
    query.data?.find(
      (row) => row.product.product_id === selectedProductId,
    ) ?? null;
  const currentVersion = selected?.versions[0] ?? null;

  const selectRow = (row: ModelRow) => {
    setSelectedProductId(row.product.product_id);
    setFeedback("");
  };

  async function handleFile(file: File | undefined) {
    setImportError("");
    setImportDocument(null);
    setImportFileName(file?.name ?? "");
    if (!file) return;
    try {
      const parsed = JSON.parse(await readFile(file)) as unknown;
      if (!isTslDocument(parsed)) {
        throw new Error("JSON 缺少标准 TSL 顶层字段");
      }
      setImportDocument(parsed);
    } catch (error) {
      setImportError(
        error instanceof Error ? error.message : "无法读取 TSL JSON",
      );
    }
  }

  async function importDraft(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (importLock.current) return;
    if (!importName.trim()) {
      setImportError("请输入产品名称");
      return;
    }
    if (!importDocument) {
      setImportError("请选择有效的 TSL JSON 文件");
      return;
    }
    importLock.current = true;
    setImportBusy(true);
    setImportError("");
    try {
      const result = await api.importThingModel(
        importName.trim(),
        importDocument,
      );
      setFeedback(
        `草稿已导入：${result.product.name} / v${result.model.version}`,
      );
      setSelectedProductId(result.product.product_id);
      setImportOpen(false);
      setImportName("");
      setImportDocument(null);
      setImportFileName("");
      query.reload();
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "导入失败");
    } finally {
      importLock.current = false;
      setImportBusy(false);
    }
  }

  async function runModelAction(action: ModelAction) {
    if (!currentVersion || actionLock.current) return;
    actionLock.current = true;
    setActionBusy(action);
    setFeedback(
      action === "validate"
        ? "正在校验…"
        : action === "export"
          ? "正在准备导出…"
          : action === "publish"
            ? "正在发布草稿…"
            : "正在归档草稿…",
    );
    try {
      if (action === "validate") {
        await api.validateThingModel(currentVersion.model_version_id);
        setFeedback(
          `版本 v${currentVersion.version} 通过标准 TSL 校验`,
        );
      } else if (action === "publish") {
        await api.publishThingModel(currentVersion.model_version_id);
        setFeedback(`版本 v${currentVersion.version} 已发布`);
        query.reload();
      } else if (action === "archive") {
        await api.archiveThingModel(currentVersion.model_version_id);
        setFeedback(`版本 v${currentVersion.version} 已归档`);
        query.reload();
      } else {
        const tsl = await api.exportThingModel(
          currentVersion.model_version_id,
        );
        downloadJson(
          `${selected?.product.product_key ?? "thing-model"}-v${currentVersion.version}.json`,
          tsl,
        );
        setFeedback(`版本 v${currentVersion.version} 已导出`);
      }
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "操作失败");
    } finally {
      actionLock.current = false;
      setActionBusy(null);
    }
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="CONTROL CONTRACT / TSL"
        title="物模型"
        description="产品版本定义属性、服务和事件；人工导入先形成草稿，经校验与发布后才会绑定设备。"
        actions={
          <button
            className="button button--primary"
            type="button"
            aria-expanded={importOpen}
            onClick={() => {
              setImportOpen((value) => !value);
              setImportError("");
            }}
          >
            {importOpen ? "收起导入" : "导入 TSL 草稿"}
          </button>
        }
      />

      {importOpen ? (
        <section className="work-surface model-import" aria-label="导入 TSL 草稿">
          <div className="section-heading">
            <div>
              <p className="eyebrow">IMMUTABLE DRAFT</p>
              <h2>从标准 JSON 创建新版本</h2>
            </div>
            <span className="section-note">发布前不会影响设备控制契约。</span>
          </div>
          <form className="model-import__form" onSubmit={importDraft}>
            <label>
              产品名称
              <input
                value={importName}
                onChange={(event) => setImportName(event.target.value)}
                placeholder="例如：客厅调光器"
                disabled={importBusy}
              />
            </label>
            <label>
              TSL JSON 文件
              <input
                type="file"
                accept="application/json,.json"
                disabled={importBusy}
                onChange={(event) => {
                  void handleFile(event.currentTarget.files?.[0]);
                }}
              />
            </label>
            <div className="model-import__submit">
              <span className="mono">
                {importFileName || "尚未选择文件"}
              </span>
              <button
                className="button button--primary"
                type="submit"
                disabled={importBusy}
              >
                {importBusy ? "正在导入…" : "创建草稿"}
              </button>
            </div>
          </form>
          {importError ? (
            <p className="inline-result inline-result--error" role="alert">
              {importError}
            </p>
          ) : null}
        </section>
      ) : null}

      {feedback ? (
        <p className="inline-result model-feedback" role="status">
          {feedback}
        </p>
      ) : null}
      {query.loading ? (
        <PageState state="loading" label="正在读取产品与版本" />
      ) : null}
      {query.error ? (
        <ErrorState error={query.error} onRetry={query.reload} />
      ) : null}
      {query.data?.length === 0 ? (
        <PageState state="empty" label="尚未导入物模型" />
      ) : null}
      {query.data?.length ? (
        <div className="split-workbench">
          <section className="work-surface list-pane" aria-label="物模型列表">
            <div className="section-heading">
              <div>
                <p className="eyebrow">PRODUCTS</p>
                <h2>{query.data.length} 个控制契约</h2>
              </div>
            </div>
            {query.data.map((row) => (
              <button
                type="button"
                className={`select-row${selectedProductId === row.product.product_id ? " select-row--active" : ""}`}
                key={row.product.product_id}
                onClick={() => selectRow(row)}
              >
                <span>
                  <strong>{row.product.name}</strong>
                  <span className="mono">{row.product.product_key}</span>
                </span>
                <span className="select-row__meta">
                  v{row.versions[0]?.version ?? "—"}
                  <StatusBadge
                    value={row.versions[0]?.status ?? "unknown"}
                  />
                </span>
              </button>
            ))}
          </section>
          <section className="work-surface detail-pane">
            {!selected ? (
              <PageState state="empty" label="选择一个产品查看 TSL" />
            ) : (
              <>
                <div className="section-heading model-detail-heading">
                  <div>
                    <p className="eyebrow mono">
                      {selected.product.product_key}
                    </p>
                    <h2>{selected.product.name}</h2>
                  </div>
                  <div className="model-actions" aria-label="版本操作">
                    <button
                      className="button button--secondary"
                      disabled={!currentVersion || actionBusy !== null}
                      onClick={() => void runModelAction("validate")}
                    >
                      {actionBusy === "validate"
                        ? "正在校验…"
                        : "校验当前版本"}
                    </button>
                    <button
                      className="button button--secondary"
                      disabled={!currentVersion || actionBusy !== null}
                      onClick={() => void runModelAction("export")}
                    >
                      {actionBusy === "export"
                        ? "正在导出…"
                        : "导出 JSON"}
                    </button>
                    <button
                      className="button button--primary"
                      disabled={
                        currentVersion?.status !== "draft"
                        || actionBusy !== null
                      }
                      onClick={() => void runModelAction("publish")}
                    >
                      {actionBusy === "publish"
                        ? "正在发布…"
                        : "发布草稿"}
                    </button>
                    <button
                      className="button button--danger"
                      disabled={
                        currentVersion?.status !== "draft"
                        || actionBusy !== null
                      }
                      onClick={() => void runModelAction("archive")}
                    >
                      {actionBusy === "archive"
                        ? "正在归档…"
                        : "归档草稿"}
                    </button>
                  </div>
                </div>
                {selected.versions.map((version) => (
                  <div
                    className="model-inspector"
                    key={version.model_version_id}
                  >
                    <dl className="definition-grid">
                      <div>
                        <dt>版本</dt>
                        <dd>v{version.version}</dd>
                      </div>
                      <div>
                        <dt>状态</dt>
                        <dd>
                          <StatusBadge value={version.status} />
                        </dd>
                      </div>
                      <div>
                        <dt>创建时间</dt>
                        <dd className="mono">
                          {formatDateTime(version.created_at)}
                        </dd>
                      </div>
                      <div>
                        <dt>指纹</dt>
                        <dd className="mono">
                          {selected.product.capability_fingerprint}
                        </dd>
                      </div>
                    </dl>
                    <div className="capability-tabs" aria-label="能力数量">
                      <span>
                        <strong>{version.tsl_json.properties.length}</strong>{" "}
                        属性
                      </span>
                      <span>
                        <strong>{version.tsl_json.services.length}</strong>{" "}
                        服务
                      </span>
                      <span>
                        <strong>{version.tsl_json.events.length}</strong>{" "}
                        事件
                      </span>
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

async function readFile(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result ?? "")));
    reader.addEventListener("error", () => reject(new Error("无法读取文件")));
    reader.readAsText(file);
  });
}

function isTslDocument(value: unknown): value is TslDocument {
  if (value === null || typeof value !== "object") return false;
  const candidate = value as Partial<TslDocument>;
  return (
    typeof candidate.schema === "string"
    && candidate.profile !== null
    && typeof candidate.profile === "object"
    && Array.isArray(candidate.properties)
    && Array.isArray(candidate.services)
    && Array.isArray(candidate.events)
  );
}

function downloadJson(fileName: string, value: TslDocument) {
  const anchor = document.createElement("a");
  anchor.download = fileName;
  anchor.href = `data:application/json;charset=utf-8,${encodeURIComponent(
    JSON.stringify(value, null, 2),
  )}`;
  anchor.click();
}

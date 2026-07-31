import { useState } from "react";

import type {
  OperationResult,
  TslDataType,
  TslParameter,
  TslProperty,
  TslService,
} from "../api/types";
import { StatusBadge } from "./StatusBadge";
import { formatValue } from "./format";
import {
  initialTslInput,
  normalizeNumericSpecs,
  parseTslInput,
} from "./tslInput";

interface Draft {
  value: string | boolean;
  touched: boolean;
  error: string;
}

function DataInput({
  id,
  label,
  type,
  draft,
  required,
  onChange,
}: {
  id: string;
  label: string;
  type: TslDataType;
  draft: Draft;
  required: boolean;
  onChange: (value: string | boolean) => void;
}) {
  const errorId = `${id}-error`;
  const describedBy = draft.error ? errorId : undefined;
  if (type.type === "bool") {
    return (
      <label className="toggle-control" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={Boolean(draft.value)}
          required={required}
          aria-invalid={Boolean(draft.error)}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span aria-hidden="true" />
        <strong>{label}</strong>
        {draft.error ? <small className="input-error" id={errorId}>{draft.error}</small> : null}
      </label>
    );
  }
  if (type.type === "enum" && !Array.isArray(type.specs)) {
    return (
      <label htmlFor={id}>
        <span>{label}</span>
        <select
          id={id}
          value={String(draft.value)}
          required={required}
          aria-invalid={Boolean(draft.error)}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="" disabled>请选择</option>
          {Object.entries(type.specs).map(([key, name]) => (
            <option key={key} value={key}>{String(name)} · {key}</option>
          ))}
        </select>
        {draft.error ? <small className="input-error" id={errorId}>{draft.error}</small> : null}
      </label>
    );
  }
  const numeric = ["int", "float", "double"].includes(type.type);
  const json = type.type === "struct" || type.type === "array";
  const specs = normalizeNumericSpecs(type);
  const common = {
    id,
    value: String(draft.value),
    required,
    "aria-invalid": Boolean(draft.error),
    "aria-describedby": describedBy,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      onChange(event.target.value),
  };
  return (
    <label htmlFor={id}>
      <span>{label}</span>
      {json ? (
        <textarea {...common} rows={4} spellCheck={false} />
      ) : (
        <input
          {...common}
          type={numeric ? "number" : type.type === "date" ? "datetime-local" : "text"}
          min={numeric ? specs.min : undefined}
          max={numeric ? specs.max : undefined}
          step={numeric ? specs.step : undefined}
        />
      )}
      {type.type === "date" ? <small className="input-help">按本地时间输入，提交为 epoch 毫秒。</small> : null}
      {draft.error ? <small className="input-error" id={errorId}>{draft.error}</small> : null}
    </label>
  );
}

export function PropertyControl({
  property,
  currentValue,
  risk,
  onSubmit,
}: {
  property: TslProperty;
  currentValue: unknown;
  risk: string;
  onSubmit: (value: unknown) => Promise<OperationResult>;
}) {
  const [draft, setDraft] = useState<Draft>(() => ({
    value: initialTslInput(currentValue, property.dataType),
    touched: false,
    error: "",
  }));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const id = `property-${property.identifier}`;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    let parsed;
    try {
      parsed = parseTslInput(draft.value, property.dataType, {
        required: true,
        touched: true,
      });
    } catch (error) {
      setDraft((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "输入无效",
      }));
      return;
    }
    setBusy(true);
    setResult("");
    try {
      const operation = await onSubmit(parsed.value);
      setResult(`执行结果：${operation.status} · ${operation.operation_id}`);
    } catch (error) {
      setResult(`执行失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="control-row" onSubmit={submit} noValidate>
      <div className="control-row__identity"><strong>{property.name}</strong><span className="mono">{property.identifier}</span></div>
      <div className="control-row__current"><span>当前观测</span><strong>{formatValue(currentValue)}</strong></div>
      <DataInput
        id={id}
        label="目标值"
        type={property.dataType}
        draft={draft}
        required
        onChange={(value) => setDraft({ value, touched: true, error: "" })}
      />
      <StatusBadge value={risk} />
      <button className="button button--primary" disabled={busy} type="submit">{busy ? "发送中…" : "直接写入"}</button>
      <p className="control-row__result" aria-live="polite">{result}</p>
    </form>
  );
}

export function ServiceControl({
  service,
  risk,
  onSubmit,
}: {
  service: TslService;
  risk: string;
  onSubmit: (inputs: Record<string, unknown>) => Promise<OperationResult>;
}) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() =>
    Object.fromEntries(
      service.inputData.map((item) => [
        item.identifier,
        { value: initialTslInput(undefined, item.dataType), touched: false, error: "" },
      ]),
    ),
  );
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const inputs: Record<string, unknown> = {};
    const errors: Record<string, string> = {};
    for (const item of service.inputData) {
      try {
        const parsed = parseTslInput(drafts[item.identifier].value, item.dataType, {
          required: Boolean(item.required),
          touched: drafts[item.identifier].touched,
        });
        if (parsed.present) inputs[item.identifier] = parsed.value;
      } catch (error) {
        errors[item.identifier] = error instanceof Error ? error.message : "输入无效";
      }
    }
    if (Object.keys(errors).length) {
      setDrafts((current) =>
        Object.fromEntries(
          Object.entries(current).map(([key, draft]) => [
            key,
            { ...draft, error: errors[key] ?? "" },
          ]),
        ),
      );
      return;
    }
    setBusy(true);
    setResult("");
    try {
      const operation = await onSubmit(inputs);
      setResult(`执行结果：${operation.status} · ${operation.operation_id}`);
    } catch (error) {
      setResult(`执行失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="service-control" onSubmit={submit} noValidate>
      <div className="service-control__header">
        <div><strong>{service.name}</strong><span className="mono">{service.identifier}</span></div>
        <StatusBadge value={risk} />
      </div>
      <div className="form-grid">
        {service.inputData.length === 0 ? <p className="muted">此服务无需参数。</p> : service.inputData.map((parameter: TslParameter) => (
          <DataInput
            key={parameter.identifier}
            id={`service-${service.identifier}-${parameter.identifier}`}
            label={`${parameter.name}${parameter.required ? " *" : ""}`}
            type={parameter.dataType}
            draft={drafts[parameter.identifier]}
            required={Boolean(parameter.required)}
            onChange={(value) =>
              setDrafts((current) => ({
                ...current,
                [parameter.identifier]: { value, touched: true, error: "" },
              }))
            }
          />
        ))}
      </div>
      <div className="service-control__footer">
        <p aria-live="polite">{result}</p>
        <button className="button button--primary" disabled={busy} type="submit">{busy ? "调用中…" : "直接调用服务"}</button>
      </div>
    </form>
  );
}

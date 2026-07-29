import { useState } from "react";

import type { Operation, TslDataType, TslParameter, TslProperty, TslService } from "../api/types";
import { StatusBadge } from "./StatusBadge";
import { formatValue } from "./format";

function initialInput(value: unknown, type: TslDataType): string | boolean {
  if (type.type === "bool") return Boolean(value);
  return value === undefined || value === null ? "" : String(value);
}

function parseInput(value: string | boolean, type: TslDataType): unknown {
  if (type.type === "bool") return Boolean(value);
  if (["int", "float", "double"].includes(type.type)) return Number(value);
  return value;
}

function DataInput({
  id,
  label,
  type,
  value,
  onChange,
}: {
  id: string;
  label: string;
  type: TslDataType;
  value: string | boolean;
  onChange: (value: string | boolean) => void;
}) {
  if (type.type === "bool") {
    return (
      <label className="toggle-control" htmlFor={id}>
        <input id={id} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        <span aria-hidden="true" />
        <strong>{label}</strong>
      </label>
    );
  }
  if (type.type === "enum" && !Array.isArray(type.specs)) {
    return (
      <label htmlFor={id}><span>{label}</span>
        <select id={id} value={String(value)} onChange={(event) => onChange(event.target.value)}>
          {Object.entries(type.specs).map(([key, name]) => <option key={key} value={key}>{String(name)} · {key}</option>)}
        </select>
      </label>
    );
  }
  const specs = Array.isArray(type.specs) ? {} : type.specs;
  return (
    <label htmlFor={id}><span>{label}</span>
      <input
        id={id}
        type={["int", "float", "double"].includes(type.type) ? "number" : "text"}
        value={String(value)}
        min={typeof specs.min === "number" ? specs.min : undefined}
        max={typeof specs.max === "number" ? specs.max : undefined}
        step={typeof specs.step === "number" ? specs.step : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
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
  onSubmit: (value: unknown) => Promise<Operation>;
}) {
  const [value, setValue] = useState<string | boolean>(() => initialInput(currentValue, property.dataType));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const id = `property-${property.identifier}`;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setResult("");
    try {
      const operation = await onSubmit(parseInput(value, property.dataType));
      setResult(`执行结果：${operation.status} · ${operation.operation_id}`);
    } catch (error) {
      setResult(`执行失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="control-row" onSubmit={submit}>
      <div className="control-row__identity"><strong>{property.name}</strong><span className="mono">{property.identifier}</span></div>
      <div className="control-row__current"><span>当前观测</span><strong>{formatValue(currentValue)}</strong></div>
      <DataInput id={id} label="目标值" type={property.dataType} value={value} onChange={setValue} />
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
  onSubmit: (inputs: Record<string, unknown>) => Promise<Operation>;
}) {
  const [values, setValues] = useState<Record<string, string | boolean>>(() =>
    Object.fromEntries(service.inputData.map((item) => [item.identifier, initialInput(undefined, item.dataType)])),
  );
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setResult("");
    const inputs = Object.fromEntries(
      service.inputData.map((item) => [item.identifier, parseInput(values[item.identifier], item.dataType)]),
    );
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
    <form className="service-control" onSubmit={submit}>
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
            value={values[parameter.identifier]}
            onChange={(value) => setValues((current) => ({ ...current, [parameter.identifier]: value }))}
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

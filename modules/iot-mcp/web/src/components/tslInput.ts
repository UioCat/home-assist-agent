import type { TslDataType } from "../api/types";

export interface ParsedTslInput {
  present: boolean;
  value?: unknown;
}

export interface NumericSpecs {
  min?: number;
  max?: number;
  step?: number;
}

export function initialTslInput(value: unknown, type: TslDataType): string | boolean {
  if (type.type === "bool") return Boolean(value);
  if (type.type === "date" && value !== undefined && value !== null) {
    const date = new Date(typeof value === "number" ? value : String(value));
    if (!Number.isNaN(date.valueOf())) {
      const local = new Date(date.valueOf() - date.getTimezoneOffset() * 60_000);
      return local.toISOString().slice(0, 16);
    }
  }
  if (type.type === "struct" || type.type === "array") {
    return value === undefined || value === null ? "" : JSON.stringify(value, null, 2);
  }
  return value === undefined || value === null ? "" : String(value);
}

export function normalizeNumericSpecs(type: TslDataType): NumericSpecs {
  if (Array.isArray(type.specs)) return {};
  const result: NumericSpecs = {};
  for (const key of ["min", "max", "step"] as const) {
    const raw = type.specs[key];
    if (raw === undefined || raw === null || raw === "") continue;
    const value = Number(raw);
    if (Number.isFinite(value)) result[key] = value;
  }
  return result;
}

export function parseTslInput(
  raw: string | boolean,
  type: TslDataType,
  options: { required: boolean; touched: boolean },
): ParsedTslInput {
  if (!options.touched && !options.required) return { present: false };
  if (type.type === "bool") return { present: true, value: Boolean(raw) };

  const text = String(raw);
  if (!text.trim()) {
    if (options.required) throw new Error("此字段为必填项");
    if (type.type === "text" && options.touched) {
      return { present: true, value: "" };
    }
    return { present: false };
  }

  if (type.type === "int" || type.type === "float" || type.type === "double") {
    const value = Number(text);
    if (!Number.isFinite(value)) throw new Error("请输入有限数值");
    if (type.type === "int" && !Number.isInteger(value)) throw new Error("请输入整数");
    const { min, max, step } = normalizeNumericSpecs(type);
    if (min !== undefined && value < min) throw new Error(`不能小于 ${min}`);
    if (max !== undefined && value > max) throw new Error(`不能大于 ${max}`);
    if (step !== undefined && step > 0) {
      const base = min ?? 0;
      const quotient = (value - base) / step;
      if (Math.abs(quotient - Math.round(quotient)) > 1e-9) {
        throw new Error(`必须按 ${step} 的步长取值`);
      }
    }
    return { present: true, value };
  }

  if (type.type === "date") {
    const value = new Date(text).valueOf();
    if (!Number.isFinite(value)) throw new Error("请输入有效日期时间");
    return { present: true, value };
  }

  if (type.type === "struct" || type.type === "array") {
    let value: unknown;
    try {
      value = JSON.parse(text);
    } catch {
      throw new Error("请输入有效 JSON");
    }
    if (type.type === "struct" && (value === null || typeof value !== "object" || Array.isArray(value))) {
      throw new Error("请输入 JSON 对象");
    }
    if (type.type === "array" && !Array.isArray(value)) {
      throw new Error("请输入 JSON 数组");
    }
    return { present: true, value };
  }

  if (type.type === "enum" && !Array.isArray(type.specs) && !(text in type.specs)) {
    throw new Error("请选择有效枚举值");
  }
  return { present: true, value: text };
}

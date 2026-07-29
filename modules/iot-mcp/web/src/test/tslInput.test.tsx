import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { TslDataType, TslService } from "../api/types";
import { ServiceControl } from "../components/CapabilityControls";
import { normalizeNumericSpecs, parseTslInput } from "../components/tslInput";

const type = (name: TslDataType["type"], specs: TslDataType["specs"] = {}): TslDataType => ({
  type: name,
  specs,
});

describe("TSL input parsing", () => {
  it("normalizes numeric string constraints and enforces finite integer range and step", () => {
    const intType = type("int", { min: "5", max: "20", step: "5" });
    expect(normalizeNumericSpecs(intType)).toEqual({ min: 5, max: 20, step: 5 });
    expect(parseTslInput("10", intType, { required: true, touched: true })).toEqual({
      present: true,
      value: 10,
    });
    expect(() => parseTslInput("", intType, { required: true, touched: true })).toThrow("必填");
    expect(() => parseTslInput("Infinity", intType, { required: true, touched: true })).toThrow("有限");
    expect(() => parseTslInput("5.5", intType, { required: true, touched: true })).toThrow("整数");
    expect(() => parseTslInput("4", intType, { required: true, touched: true })).toThrow("小于");
    expect(() => parseTslInput("11", intType, { required: true, touched: true })).toThrow("步长");
  });

  it("omits untouched optional values and parses date, struct, and array explicitly", () => {
    expect(parseTslInput("", type("text"), { required: false, touched: false })).toEqual({
      present: false,
    });
    const date = parseTslInput("2026-07-29T16:30", type("date"), {
      required: true,
      touched: true,
    });
    expect(typeof date.value).toBe("number");
    expect(parseTslInput('{"enabled":true}', type("struct"), { required: true, touched: true }).value)
      .toEqual({ enabled: true });
    expect(parseTslInput("[1,2]", type("array"), { required: true, touched: true }).value)
      .toEqual([1, 2]);
    expect(() => parseTslInput("[]", type("struct"), { required: true, touched: true })).toThrow("对象");
    expect(() => parseTslInput("{}", type("array"), { required: true, touched: true })).toThrow("数组");
  });

  it.each(["int", "float", "double", "date", "struct", "array"] as const)(
    "omits a touched then cleared optional %s instead of sending an empty string",
    (dataType) => {
      expect(
        parseTslInput("", type(dataType), { required: false, touched: true }),
      ).toEqual({ present: false });
    },
  );

  it.each(["int", "date", "struct", "array"] as const)(
    "rejects a touched then cleared required %s",
    (dataType) => {
      expect(() =>
        parseTslInput("", type(dataType), { required: true, touched: true }),
      ).toThrow("必填");
    },
  );

  it("preserves an explicitly cleared optional text value", () => {
    expect(parseTslInput("", type("text"), { required: false, touched: true })).toEqual({
      present: true,
      value: "",
    });
  });

  it("blocks invalid high-risk service input and omits untouched optional inputs", async () => {
    const service: TslService = {
      identifier: "TemporaryUnlock",
      name: "临时解锁",
      inputData: [
        {
          identifier: "duration",
          name: "持续秒数",
          required: true,
          dataType: type("int", { min: "5", max: "120", step: "5" }),
        },
        {
          identifier: "note",
          name: "备注",
          required: false,
          dataType: type("text"),
        },
      ],
      outputData: [],
    };
    const submit = vi.fn().mockResolvedValue({ operation_id: "op", device_id: "door", status: "succeeded" });
    const user = userEvent.setup();
    render(<ServiceControl service={service} risk="high" onSubmit={submit} />);

    await user.click(screen.getByRole("button", { name: "直接调用服务" }));
    expect(await screen.findByText("此字段为必填项")).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();

    await user.type(screen.getByRole("spinbutton", { name: /持续秒数/ }), "10");
    await user.click(screen.getByRole("button", { name: "直接调用服务" }));
    expect(submit).toHaveBeenCalledWith({ duration: 10 });
  });

  it("omits a cleared optional structured value and blocks a cleared required one", async () => {
    const service: TslService = {
      identifier: "Configure",
      name: "配置",
      inputData: [
        {
          identifier: "samples",
          name: "采样",
          required: false,
          dataType: type("array"),
        },
        {
          identifier: "policy",
          name: "策略",
          required: true,
          dataType: type("struct"),
        },
      ],
      outputData: [],
    };
    const submit = vi.fn().mockResolvedValue({
      operation_id: "op",
      device_id: "door",
      status: "succeeded",
    });
    const user = userEvent.setup();
    render(<ServiceControl service={service} risk="high" onSubmit={submit} />);

    await user.type(screen.getByLabelText("采样"), "[1]");
    await user.clear(screen.getByLabelText("采样"));
    fireEvent.change(screen.getByLabelText(/策略/), {
      target: { value: '{"mode":"safe"}' },
    });
    await user.clear(screen.getByLabelText(/策略/));
    await user.click(screen.getByRole("button", { name: "直接调用服务" }));
    expect(submit).not.toHaveBeenCalled();
    expect(await screen.findByText("此字段为必填项")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/策略/), {
      target: { value: '{"mode":"safe"}' },
    });
    await user.click(screen.getByRole("button", { name: "直接调用服务" }));
    expect(submit).toHaveBeenCalledWith({ policy: { mode: "safe" } });
  });
});

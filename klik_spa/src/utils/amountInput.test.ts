import { describe, it, expect } from "vitest";
import { stripAmountInput, formatAmountInput } from "./amountInput";

describe("stripAmountInput", () => {
  it("keeps plain digits untouched", () => {
    expect(stripAmountInput("349903")).toBe("349903");
  });

  it("removes separators the user typed or pasted", () => {
    expect(stripAmountInput("349,903.00")).toBe("349903.00");
    expect(stripAmountInput("KES 1 500")).toBe("1500");
  });

  it("collapses multiple dots instead of producing NaN", () => {
    expect(stripAmountInput("1.2.3")).toBe("1.23");
  });

  it("truncates past two decimals rather than rounding up", () => {
    // Rounding would let someone allocate a cent more than they typed.
    expect(stripAmountInput("10.999")).toBe("10.99");
  });

  it("drops a minus sign — a received payment is never negative", () => {
    expect(stripAmountInput("-500")).toBe("500");
  });

  it("returns empty for input with nothing numeric in it", () => {
    expect(stripAmountInput("")).toBe("");
    expect(stripAmountInput("abc")).toBe("");
  });

  it("preserves a trailing dot so typing decimals is not interrupted", () => {
    expect(stripAmountInput("1500.")).toBe("1500.");
  });

  it("always yields something Number() can parse, or empty", () => {
    // This is the property that matters. Number("349,903") is NaN, and a NaN amount previously
    // slipped past every `<= 0` guard in the allocation split because NaN fails all comparisons.
    const inputs = ["349,903.00", "1.2.3", "-500", "abc", "", "10.999", "1500.", "0"];
    for (const raw of inputs) {
      const stripped = stripAmountInput(raw);
      if (stripped === "") continue;
      expect(Number.isNaN(Number(stripped))).toBe(false);
    }
  });
});

describe("formatAmountInput", () => {
  it("groups thousands", () => {
    expect(formatAmountInput("349903")).toBe("349,903");
    expect(formatAmountInput("1000000")).toBe("1,000,000");
  });

  it("leaves small numbers alone", () => {
    expect(formatAmountInput("500")).toBe("500");
  });

  it("keeps decimals exactly as typed, without padding them", () => {
    // Padding "1500.5" to "1500.50" mid-keystroke would move the caret past the user's cursor.
    expect(formatAmountInput("1500.5")).toBe("1,500.5");
    expect(formatAmountInput("1500.50")).toBe("1,500.50");
  });

  it("preserves a trailing dot", () => {
    expect(formatAmountInput("1500.")).toBe("1,500.");
  });

  it("returns empty for empty input", () => {
    expect(formatAmountInput("")).toBe("");
  });

  it("round-trips back through strip to the same raw value", () => {
    for (const raw of ["349903", "1500.5", "1000000.25", "0", "1500."]) {
      expect(stripAmountInput(formatAmountInput(raw))).toBe(raw);
    }
  });
});

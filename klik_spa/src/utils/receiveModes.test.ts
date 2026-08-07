import { describe, expect, it } from "vitest";
import { defaultReceiveMode, requiresReference, selectableReceiveModes } from "./receiveModes";
import type { PaymentMode } from "../hooks/usePaymentModes";

const mode = (
  mode_of_payment: string,
  type: string,
  account_type: string | null = "Cash",
  isDefault = 0
): PaymentMode => ({ mode_of_payment, type, account_type, default: isDefault });

describe("selectableReceiveModes", () => {
  it("drops Phone modes, which have no receive integration yet", () => {
    const result = selectableReceiveModes([
      mode("Cash", "Cash"),
      mode("Mpesa-111222", "Phone", "Bank"),
    ]);
    expect(result.map((m) => m.mode_of_payment)).toEqual(["Cash"]);
  });

  it("keeps Bank and Default modes", () => {
    const result = selectableReceiveModes([
      mode("Cash", "Cash"),
      mode("Credit Card", "Bank", "Bank"),
      mode("Vouchers", "Default", null),
    ]);
    expect(result).toHaveLength(3);
  });

  it("returns an empty list rather than throwing on no modes", () => {
    expect(selectableReceiveModes([])).toEqual([]);
  });
});

describe("defaultReceiveMode", () => {
  it("prefers the profile default", () => {
    expect(
      defaultReceiveMode([mode("Cash", "Cash"), mode("Credit Card", "Bank", "Bank", 1)])
    ).toBe("Credit Card");
  });

  it("falls back to the first mode when none is flagged default", () => {
    expect(defaultReceiveMode([mode("Cash", "Cash"), mode("Credit Card", "Bank", "Bank")])).toBe(
      "Cash"
    );
  });

  it("never returns a filtered-out Phone mode, even when it is the profile default", () => {
    // The trap: computing the default before filtering leaves the select empty.
    expect(
      defaultReceiveMode([mode("Mpesa-111222", "Phone", "Bank", 1), mode("Cash", "Cash")])
    ).toBe("Cash");
  });

  it("returns an empty string when nothing is selectable", () => {
    expect(defaultReceiveMode([mode("Mpesa-111222", "Phone", "Bank", 1)])).toBe("");
  });
});

describe("requiresReference", () => {
  it("is true for a Bank account", () => {
    expect(requiresReference([mode("Credit Card", "Bank", "Bank")], "Credit Card")).toBe(true);
  });

  it("is false for a Cash account", () => {
    expect(requiresReference([mode("Cash", "Cash", "Cash")], "Cash")).toBe(false);
  });

  it("is false for an unknown or unselected mode", () => {
    expect(requiresReference([mode("Cash", "Cash", "Cash")], "")).toBe(false);
    expect(requiresReference([mode("Cash", "Cash", "Cash")], "Nonexistent")).toBe(false);
  });

  it("is false when the backend could not resolve an account type", () => {
    expect(requiresReference([mode("Vouchers", "Default", null)], "Vouchers")).toBe(false);
  });
});

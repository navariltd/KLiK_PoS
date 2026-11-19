import { afterEach, describe, expect, it, vi } from "vitest";
import { checkoutHeldOrder, createHeldOrder, HeldOrderGoneError } from "./salesOrder";

afterEach(() => vi.unstubAllGlobals());

const reply = (message: unknown) =>
  vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ message }) });

describe("held order calls", () => {
  it.each([
    ["checkout", () => checkoutHeldOrder("SAL-ORD-1", {})],
    ["hold", () => createHeldOrder({ held_order_id: "SAL-ORD-1" })],
  ])("%s raises a typed error when the held order is gone", async (_label, call) => {
    vi.stubGlobal("window", { csrf_token: "t" });
    vi.stubGlobal(
      "fetch",
      reply({ success: false, code: "held_order_gone", order_id: "SAL-ORD-1", message: "no longer exists" }),
    );

    const err = await call().catch((e) => e);

    expect(err).toBeInstanceOf(HeldOrderGoneError);
    expect(err.orderId).toBe("SAL-ORD-1");
    expect(err.message).toBe("no longer exists");
  });

  it("keeps a plain error for any other refusal", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    vi.stubGlobal("fetch", reply({ success: false, message: "Stock is short" }));

    const err = await checkoutHeldOrder("SAL-ORD-1", {}).catch((e) => e);

    expect(err).not.toBeInstanceOf(HeldOrderGoneError);
    expect(err.message).toBe("Stock is short");
  });
});

describe("a held checkout the server refused", () => {
  // Unmarked, the payment dialog kept the checkout "pending" and the cart's recovery poll held
  // the Checkout button on "Checking cart..." for up to 30s after a price-floor refusal.
  it("says a response came back, so the dialog drops the attempt", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    vi.stubGlobal("fetch", reply({ success: false, message: "Rate is below the minimum" }));

    const err = await checkoutHeldOrder("SAL-ORD-1", {}).catch((e) => e);

    expect(err.checkoutResponseReceived).toBe(true);
    expect(err.checkoutResponse).toMatchObject({ success: false });
  });

  it("says nothing came back when the connection dropped, so recovery still runs", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const err = await checkoutHeldOrder("SAL-ORD-1", {}).catch((e) => e);

    expect(err.checkoutResponseReceived).toBeUndefined();
  });
});

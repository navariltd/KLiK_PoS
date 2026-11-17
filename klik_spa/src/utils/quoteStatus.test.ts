import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("react-toastify", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() }) }));
vi.mock("../services/quotation", () => ({
  getQuoteDetails: vi.fn(async () => ({ success: true, copy: true, name: "SAL-QTN-1", customer: "Acme", items: [{ item_code: "PEN", item_name: "Pen", price: 12, quantity: 3, uom: "Nos" }] })),
}));

import { quoteStatusLabel } from "./quoteStatus";
import { addQuoteCopyToCart } from "./quoteToCart";
import { getOriginalQuotationId, getCachedDraftInvoiceItems } from "./draftInvoiceCache";
import { getQuoteDetails } from "../services/quotation";
import { useCartStore } from "../stores/cartStore";

describe("quoteStatusLabel", () => {
  it("names each state the way the till reads it", () => {
    expect(quoteStatusLabel({ state: "open", expired: false })).toBe("Quote");
    expect(quoteStatusLabel({ state: "open", expired: true })).toBe("Expired");
    expect(quoteStatusLabel({ state: "converted", invoice: "ACC-SINV-1", status: "Open" })).toBe("Converted");
    expect(quoteStatusLabel({ state: "converted", invoice: null, status: "Ordered" })).toBe("Ordered");
    expect(quoteStatusLabel({ state: "closed", status: "Lost", docstatus: 1 })).toBe("Lost");
    expect(quoteStatusLabel({ state: "closed", status: "Cancelled", docstatus: 2 })).toBe("Cancelled");
  });
});

describe("addQuoteCopyToCart", () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ message: { items: [] } }))));
    useCartStore.setState({ cartItems: [], quoteMode: false, selectedCustomer: null });
  });

  it("asks for a copy and fills the cart as a new sale, not linked to the quote", async () => {
    expect(await addQuoteCopyToCart("SAL-QTN-1")).toBe(true);
    expect(getQuoteDetails).toHaveBeenCalledWith("SAL-QTN-1", true);
    expect(useCartStore.getState().cartItems.map((i) => [i.item_code, i.quantity])).toEqual([["PEN", 3]]);
    expect(getOriginalQuotationId()).toBeNull();
    expect(getCachedDraftInvoiceItems()?.originalHeldOrderId ?? null).toBeNull();
  });
});

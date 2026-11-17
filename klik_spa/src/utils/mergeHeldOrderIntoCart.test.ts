import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("react-toastify", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() }) }));

import { toast } from "react-toastify";
import { useCartStore } from "../stores/cartStore";
import { cacheHeldOrder, getCachedDraftInvoiceItems, getOriginalHeldOrderId } from "./draftInvoiceCache";
import { mergeHeldOrderIntoCart } from "./heldOrderToCart";
import { mergedOrderIds } from "./mergeHeldOrder";
import type { CartItem } from "../../types";

const pen = { id: "PEN", item_code: "PEN", name: "Pen", price: 10, quantity: 1, uom: "Nos" } as unknown as CartItem;
const order = { customer: "Acme", items: [{ item_code: "INK", item_name: "Ink", price: 5, quantity: 2, uom: "Nos" }] };

describe("mergeHeldOrderIntoCart", () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    });
    useCartStore.setState({ cartItems: [pen], selectedCustomer: { id: "Walk In" } as never });
  });

  it("a cart from held order A takes B's lines, keeps A and its customer, and B is the one to retire", () => {
    cacheHeldOrder("SO-A", [pen], { id: "Walk In" } as never);
    mergeHeldOrderIntoCart("SO-B", order, false);
    const items = useCartStore.getState().cartItems;
    expect(items.map((i) => i.id)).toEqual(["PEN", "INK"]);
    expect(getOriginalHeldOrderId()).toBe("SO-A");
    expect(getCachedDraftInvoiceItems()?.items.map((i) => i.id)).toEqual(["PEN", "INK"]); // POS reloads these
    expect(useCartStore.getState().selectedCustomer?.id).toBe("Walk In");
    expect(mergedOrderIds(items, getOriginalHeldOrderId())).toEqual(["SO-B"]);
    expect(toast.info).toHaveBeenCalledWith(expect.stringContaining("its customer (Acme)"));
  });

  it("a cart with no held order becomes B, so nothing is left to retire", () => {
    mergeHeldOrderIntoCart("SO-B", order, false);
    const items = useCartStore.getState().cartItems;
    expect(getOriginalHeldOrderId()).toBe("SO-B");
    expect(mergedOrderIds(items, getOriginalHeldOrderId())).toEqual([]);
  });
});

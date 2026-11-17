import { describe, it, expect } from "vitest";
import { canCombine, mergeCartLines, mergedOrderIds } from "./mergeHeldOrder";
import type { CartItem } from "../../types";

const l = (id: string, code: string, qty: number, extra: Record<string, unknown> = {}) =>
  ({ id, item_code: code, name: code, price: 10, quantity: qty, uom: "Nos", ...extra }) as unknown as CartItem;

describe("mergeCartLines", () => {
  it("separate: appends every incoming line, re-keyed past the cart's ids and tagged with its order", () => {
    const out = mergeCartLines([l("PEN", "PEN", 1)], [l("PEN", "PEN", 2), l("INK", "INK", 1)], "SO-B", false);
    expect(out.map((x) => [x.id, x.quantity])).toEqual([["PEN", 1], ["PEN-1", 2], ["INK", 1]]);
    expect(out[1]).toMatchObject({ merged_from: ["SO-B"] });
    expect(out[0]).not.toHaveProperty("merged_from");
  });

  it("combine: adds qty and LoS to the cart's line of the same item and unit, keeping the cart line's rate", () => {
    const cart = [l("PEN", "PEN", 1, { price: 10, los_qty: 1 })];
    const out = mergeCartLines(cart, [l("PEN", "PEN", 2, { price: 99, los_qty: 3 }), l("INK", "INK", 1)], "SO-B", true);
    expect(out.map((x) => [x.id, x.quantity, x.price, x.los_qty])).toEqual([["PEN", 3, 10, 4], ["INK", 1, 10, undefined]]);
    expect(out[0]).toMatchObject({ merged_from: ["SO-B"] });
    expect(cart[0]!.quantity).toBe(1); // the cart passed in is not changed
  });

  it("combine: a different unit, or a line with batch/serial entries, stays a line of its own", () => {
    const cart = [l("PEN", "PEN", 1), l("BAT", "BAT", 1, { bundle_entries: [{ batch_no: "B1", qty: 1 }] })];
    const out = mergeCartLines(cart, [l("PEN", "PEN", 1, { uom: "Box" }), l("BAT", "BAT", 1)], "SO-B", true);
    expect(out.map((x) => x.id)).toEqual(["PEN", "BAT", "PEN-1", "BAT-1"]);
  });
});

describe("canCombine", () => {
  it("is true only when an incoming line could be combined", () => {
    expect(canCombine([l("PEN", "PEN", 1)], [l("PEN", "PEN", 1)])).toBe(true);
    expect(canCombine([l("PEN", "PEN", 1)], [l("PEN", "PEN", 1, { uom: "Box" })])).toBe(false);
    expect(canCombine([l("PEN", "PEN", 1)], [l("INK", "INK", 1)])).toBe(false);
  });
});

describe("mergedOrderIds", () => {
  it("lists the orders still behind the cart's lines, never the cart's own order", () => {
    const cart = [l("A", "A", 1, { merged_from: ["SO-B", "SO-C"] }), l("B", "B", 1, { merged_from: ["SO-B", "SO-A"] }), l("C", "C", 1)];
    expect(mergedOrderIds(cart, "SO-A")).toEqual(["SO-B", "SO-C"]);
    expect(mergedOrderIds([l("C", "C", 1)], "SO-A")).toEqual([]);
  });
});

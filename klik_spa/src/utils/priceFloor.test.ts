import { describe, expect, it } from "vitest";

import { authorisationStillValid, checkoutPriceList, floorBlockMessage, floorRowsKey, lineListRate, rateBoxMinimum, type FloorRow } from "./priceFloor";

const row = (over: Partial<FloorRow> = {}): FloorRow => ({
  idx: 1,
  item_code: "H4",
  item_name: "H4 Bulb",
  uom: "Nos",
  qty: 1,
  rate: 420,
  minimum: 450,
  price_list: "Retail",
  ...over,
});

describe("checkoutPriceList", () => {
  it("sends the cart's list when the till lets the cashier switch lists", () => {
    expect(checkoutPriceList({ allow_price_list_switching: 1 }, "Distributor")).toBe("Distributor");
  });

  it("sends nothing when switching is off, so the server keeps its own chain", () => {
    expect(checkoutPriceList({ allow_price_list_switching: 0 }, "Distributor")).toBeNull();
    expect(checkoutPriceList(null, "Distributor")).toBeNull();
  });

  it("sends nothing when no list is selected", () => {
    expect(checkoutPriceList({ allow_price_list_switching: 1 }, null)).toBeNull();
  });
});

describe("supervisor authorisation", () => {
  it("still covers the same below-minimum lines", () => {
    const auth = { token: "t", key: floorRowsKey([row()]) };
    expect(authorisationStillValid(auth, [row()])).toBe(true);
  });

  it("no longer covers a lowered price", () => {
    const auth = { token: "t", key: floorRowsKey([row()]) };
    expect(authorisationStillValid(auth, [row({ rate: 410 })])).toBe(false);
  });

  it("no longer covers a raised quantity or another line going below", () => {
    const auth = { token: "t", key: floorRowsKey([row()]) };
    expect(authorisationStillValid(auth, [row({ qty: 2 })])).toBe(false);
    expect(authorisationStillValid(auth, [row(), row({ idx: 2, item_code: "H7" })])).toBe(false);
  });

  it("does not depend on the order the server lists lines in", () => {
    const a = row();
    const b = row({ idx: 2, item_code: "H7" });
    expect(floorRowsKey([a, b])).toBe(floorRowsKey([b, a]));
  });

  it("is never valid without an authorisation", () => {
    expect(authorisationStillValid(null, [row()])).toBe(false);
  });
});

describe("floorBlockMessage", () => {
  it("blocks payment while a line is below its minimum and nobody authorised it", () => {
    expect(floorBlockMessage({ enforced: true, rows: [row()] }, null)).toBe(
      "Prices below the minimum - fix them or ask a supervisor to authorise"
    );
  });

  it("lets payment through once a supervisor authorised these lines", () => {
    expect(floorBlockMessage({ enforced: true, rows: [row()] }, { token: "t", key: floorRowsKey([row()]) })).toBeNull();
  });

  it("does not block a till that does not enforce, or a clean cart", () => {
    expect(floorBlockMessage({ enforced: false, rows: [] }, null)).toBeNull();
    expect(floorBlockMessage({ enforced: true, rows: [] }, null)).toBeNull();
    expect(floorBlockMessage(null, null)).toBeNull();
  });
});

describe("rateBoxMinimum", () => {
  const till = { powerpack_enforce_list_floor: 1, allow_discount_change: 0, powerpack_list_floor_discount_allowance: 0 };
  it("is the list price on a floor till with discount off", () => {
    expect(rateBoxMinimum(till, 1050)).toBe(1050);
  });
  it("takes off the till's allowance", () => {
    expect(rateBoxMinimum({ ...till, powerpack_list_floor_discount_allowance: 10 }, 1000)).toBe(900);
  });
  it("is null where the cashier may discount, the till has no floor, or there is no list price", () => {
    expect(rateBoxMinimum({ ...till, allow_discount_change: 1 }, 1050)).toBeNull();
    expect(rateBoxMinimum({ ...till, powerpack_enforce_list_floor: 0 }, 1050)).toBeNull();
    expect(rateBoxMinimum(till, 0)).toBeNull();
    expect(rateBoxMinimum(null, 1050)).toBeNull();
  });
});

describe("lineListRate", () => {
  it("is the cart list's price on a line that was not switched", () => {
    expect(lineListRate(450, "", 400)).toBe(450);
    expect(lineListRate(450, undefined, undefined)).toBe(450);
  });
  it("is the picked list's price on a line switched to another list, even a lower one", () => {
    expect(lineListRate(450, "Distributor", 400)).toBe(400);
  });
  it("is unknown (no minimum) when the picked list's price is not at hand", () => {
    expect(lineListRate(450, "Distributor", undefined)).toBe(0);
    expect(rateBoxMinimum({ powerpack_enforce_list_floor: 1 }, lineListRate(450, "Distributor", undefined))).toBeNull();
  });
});

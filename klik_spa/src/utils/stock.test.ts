import { describe, expect, it } from "vitest";
import { isItemOutOfStock } from "./stock";

const stockItem = (available: number, allowNegative = false) => ({
  is_stock_item: true,
  available,
  allow_negative_stock: allowNegative,
});

describe("isItemOutOfStock", () => {
  it("is out of stock with no quantity", () => {
    expect(isItemOutOfStock(stockItem(0))).toBe(true);
  });

  it("is in stock with quantity", () => {
    expect(isItemOutOfStock(stockItem(3))).toBe(false);
  });

  it("is never out of stock when negative stock is allowed", () => {
    expect(isItemOutOfStock(stockItem(0, true))).toBe(false);
  });

  it("is never out of stock for a service item", () => {
    expect(
      isItemOutOfStock({ is_stock_item: false, available: 0, allow_negative_stock: false }),
    ).toBe(false);
  });

  describe("when stock could not be read", () => {
    it("does not claim an item is out of stock", () => {
      // Every balance arrives as 0 in this state. Treating that as out-of-stock would grey
      // out and block the whole catalogue - worse than the empty grid it replaced.
      expect(isItemOutOfStock(stockItem(0), true)).toBe(false);
    });

    it("leaves items sellable regardless of the reported figure", () => {
      expect(isItemOutOfStock(stockItem(0, false), true)).toBe(false);
      expect(isItemOutOfStock({ is_stock_item: true, available: 0, allow_negative_stock: false }, true)).toBe(
        false,
      );
    });
  });
});

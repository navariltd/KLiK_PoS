import type { MenuItem } from "../../types";

/**
 * Single source of truth for the POS "out of stock" gate.
 *
 * An item is out of stock only when it is a stock item, has no available
 * quantity, and is NOT flagged to allow negative stock. Service items
 * (is_stock_item === false) and items with allow_negative_stock are always
 * sellable regardless of on-hand quantity.
 */
export const isItemOutOfStock = (
  item: Pick<MenuItem, "is_stock_item" | "available" | "allow_negative_stock">,
): boolean =>
  item.is_stock_item !== false &&
  (item.available ?? 0) <= 0 &&
  !item.allow_negative_stock;

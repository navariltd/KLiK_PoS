import { toast } from "react-toastify";
import { useCartStore } from "../stores/cartStore";
import type { SalesInvoice } from "../../types";

export interface ReorderResult {
  addedCount: number;
  skippedItemCodes: string[];
}

/**
 * "Reorder" a past, submitted invoice into the current cart -- used by the "Add to cart"
 * button on the Customer Detail page's invoice history.
 *
 * Deliberately does NOT reuse the old invoice's frozen rate, batch, or serial numbers: it only
 * takes the item codes and quantities from the old invoice, then re-prices and re-checks stock
 * for each one through klik_pos.api.item.item_listing.get_items -- the same endpoint (and
 * therefore the same price-list-with-customer-priority and stock rules) the normal item grid
 * uses. Items go into the cart through cartStore.addToCartWithQuantity(), the same function the
 * product grid's own "Add" button calls, so batch/serial assignment at checkout time goes
 * through the usual auto-provisioning/placeholder-batch logic -- nothing about that is
 * bypassed or duplicated here.
 *
 * An item that's since been disabled, moved out of the POS Profile's allowed item groups, or
 * (when the profile hides unavailable items) gone out of stock simply won't come back from
 * get_items and is reported back as skipped, rather than silently added with stale data.
 */
export async function reorderInvoiceToCart(
  invoice: Pick<SalesInvoice, "id" | "items">,
  customerId?: string
): Promise<ReorderResult> {
  const lineQtyByCode = new Map<string, number>();

  for (const item of invoice.items || []) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const raw = item as any;
    const code: string | undefined = raw.item_code || raw.id;
    const qty = Number(raw.qty ?? raw.quantity ?? 0);
    // Return/credit-note lines carry negative qty -- reordering those makes no sense.
    if (!code || qty <= 0) continue;
    lineQtyByCode.set(code, (lineQtyByCode.get(code) || 0) + qty);
  }

  const itemCodes = Array.from(lineQtyByCode.keys());
  if (itemCodes.length === 0) {
    toast.error("This invoice has no items that can be reordered");
    return { addedCount: 0, skippedItemCodes: [] };
  }

  const params = new URLSearchParams({
    item_codes: itemCodes.join(","),
    limit: String(itemCodes.length),
  });
  if (customerId) params.append("customer", customerId);

  const response = await fetch(
    `/api/method/klik_pos.api.item.item_listing.get_items?${params.toString()}`,
    { method: "GET", credentials: "include" }
  );

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }

  const resData = await response.json();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const freshItems: Array<Record<string, any>> = resData?.message?.items || [];

  const foundCodes = new Set(freshItems.map((i) => String(i.id)));
  const skippedItemCodes = itemCodes.filter((code) => !foundCodes.has(code));

  const { addToCartWithQuantity } = useCartStore.getState();
  let addedCount = 0;

  for (const item of freshItems) {
    const code = String(item.id);
    const qty = lineQtyByCode.get(code) || 0;
    if (qty <= 0) continue;

    await addToCartWithQuantity(
      {
        id: code,
        item_code: code,
        name: String(item.name || code),
        category: String(item.category || "General"),
        price: Number(item.price || 0),
        image: String(item.image || ""),
        available: Number(item.available || 0),
        uom: item.uom || undefined,
        has_batch_no: Boolean(item.has_batch_no),
        has_serial_no: Boolean(item.has_serial_no),
      },
      qty
    );
    addedCount += 1;
  }

  if (skippedItemCodes.length > 0) {
    toast.error(
      `${skippedItemCodes.length} item(s) from this invoice are no longer available for sale and were skipped: ${skippedItemCodes.join(", ")}`
    );
  }

  return { addedCount, skippedItemCodes };
}
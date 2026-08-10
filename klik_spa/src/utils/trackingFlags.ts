// utils/trackingFlags.ts
/**
 * `has_batch_no` / `has_serial_no` on a cart item come from the backend as
 * `1`/`0` (or `true`/`false`), but they are only actually populated when the
 * item was added via the product-tile flow (`item_listing.py` returns them,
 * `productStore.ts` passes them straight through).
 *
 * Items added via the barcode/identifier scan path
 * (`productStore.ts` `fetchItemByIdentifier` -> `item_search.py`
 * `get_item_by_identifier`) never get these flags set at all — they come
 * through as `undefined`, not `false`. `undefined` means "we don't know",
 * not "not tracked". Treating it as falsy would silently stop batch/serial
 * fetches for scanned items even when they genuinely are tracked, which is a
 * regression on the primary till workflow. Only skip the fetch when the flag
 * is explicitly and definitely absent.
 */
export function isExplicitlyNotTracked(flag: boolean | number | undefined): boolean {
  return flag === 0 || flag === false;
}

/**
 * Cursor arithmetic for the paginated product list.
 *
 * The backend's `next_offset` counts SQL rows consumed, which is not the same as the number
 * of items the caller received: `hide_unavailable_items` drops rows in Python after the
 * window is taken. Advancing the cursor by items-received therefore under-counts, and on a
 * page where every item is dropped it does not advance at all — which is what parked the
 * product grid's infinite-scroll sentinel in a permanent fetch loop.
 */

/** Server cursor when it sent one, else the caller's own count. `0` is a valid cursor. */
export function resolveNextOffset(
  serverNextOffset: number | null | undefined,
  fallback: number,
): number {
  if (typeof serverNextOffset !== "number" || !Number.isFinite(serverNextOffset)) {
    return fallback;
  }
  if (serverNextOffset < 0) {
    return fallback;
  }
  return serverNextOffset;
}

/**
 * Whether another page is worth requesting.
 *
 * `has_more` alone is not enough: it is the server's view of the SQL window, and trusting it
 * after a page that moved the cursor nowhere is exactly the infinite loop. Require real
 * forward progress too.
 */
export function shouldKeepPaginating(
  hasMore: boolean,
  offsetBefore: number,
  offsetAfter: number,
): boolean {
  if (!hasMore) {
    return false;
  }
  return offsetAfter > offsetBefore;
}

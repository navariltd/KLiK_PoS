import type { CartItem } from "../../types";

/** A cart line, with the held orders (other than the cart's own) whose lines it carries. */
type Line = CartItem & { merged_from?: string[]; los_qty?: number };

const code = (l: CartItem) => l.item_code || l.id;
const hasEntries = (l: CartItem) => (l.bundle_entries?.length ?? 0) > 0;
// Batch/serial entries can't be summed safely, so such lines never combine.
const sameLine = (a: CartItem, b: CartItem) => code(a) === code(b) && a.uom === b.uom && !hasEntries(a) && !hasEntries(b);

/** Whether any incoming line could be combined into a cart line: then the cashier is asked. */
export function canCombine(cart: CartItem[], incoming: CartItem[]): boolean {
  return incoming.some((i) => cart.some((c) => sameLine(c, i)));
}

/**
 * The cart with a held order's lines added. Combined, a line's qty and LoS go onto the cart's
 * line of the same item and unit, which keeps its own rate, list, discount and description;
 * otherwise each line is appended under an id the cart doesn't use. Every line carrying the
 * order's qty is tagged with it, so the sale knows to retire that order.
 */
// ponytail: combined qty is not re-split against stock here; checkout's stock check still applies.
export function mergeCartLines(cart: CartItem[], incoming: CartItem[], orderId: string, combine: boolean): CartItem[] {
  const out: Line[] = cart.map((c) => ({ ...c }));
  const ids = new Set(out.map((c) => c.id));
  const tag = (l: Line) => [...new Set([...(l.merged_from ?? []), orderId])];
  for (const line of incoming as Line[]) {
    const target = combine ? out.find((c) => sameLine(c, line)) : undefined;
    if (target) {
      target.quantity += line.quantity;
      if (target.los_qty || line.los_qty) target.los_qty = (target.los_qty ?? 0) + (line.los_qty ?? 0);
      target.merged_from = tag(target);
      continue;
    }
    let id = line.id;
    for (let n = 1; ids.has(id); n++) id = `${line.id}-${n}`;
    ids.add(id);
    out.push({ ...line, id, merged_from: tag(line) });
  }
  return out;
}

/** The held orders still behind the cart's lines, for the sale to retire. */
export function mergedOrderIds(cart: CartItem[], ownOrder: string | null): string[] {
  const ids = (cart as Line[]).flatMap((l) => l.merged_from ?? []);
  return [...new Set(ids)].filter((id) => id !== ownOrder);
}

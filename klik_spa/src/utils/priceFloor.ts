/**
 * Minimum selling price = the price list at a till that enforces it (cecypo_powerpack).
 *
 * Checkout's preview (validate_checkout_invoice) returns `price_floor`: the lines priced below
 * their list. The sale cannot complete until each is fixed or a supervisor authorises it with
 * their PIN; the server checks the authorisation again when the invoice is saved.
 */

export interface FloorRow {
  idx: number;
  item_code: string;
  item_name: string;
  uom: string | null;
  qty: number;
  rate: number;
  minimum: number;
  price_list: string;
}

export interface PriceFloor {
  enforced: boolean;
  rows: FloorRow[];
}

export interface FloorAuthorisation {
  token: string;
  /** floorRowsKey of the lines the supervisor saw. */
  key: string;
  supervisorName?: string;
}

/** The cart's price list for checkout and holds, when the till lets the cashier pick one. */
export function checkoutPriceList(
  posDetails: { allow_price_list_switching?: unknown } | null | undefined,
  selectedPriceList: string | null | undefined,
): string | null {
  const allowed = Boolean(Number(posDetails?.allow_price_list_switching ?? 0));
  return allowed && selectedPriceList ? selectedPriceList : null;
}

export function floorRowsKey(rows: FloorRow[]): string {
  return JSON.stringify(
    rows
      .map((r) => [r.item_code, r.uom ?? "", Number(r.qty), Number(r.rate)])
      .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
  );
}

/** A supervisor authorised these exact lines; any change to them needs a new authorisation. */
export function authorisationStillValid(auth: FloorAuthorisation | null, rows: FloorRow[]): boolean {
  return Boolean(auth) && auth!.key === floorRowsKey(rows);
}

/** Why payment is blocked by the minimum price, or null. */
export function floorBlockMessage(floor: PriceFloor | null | undefined, auth: FloorAuthorisation | null): string | null {
  if (!floor?.rows?.length) return null;
  if (authorisationStillValid(auth, floor.rows)) return null;
  return "Prices below the minimum - fix them or ask a supervisor to authorise";
}

/**
 * The list price a line's typed rate is held to: its own list's, when the line was switched to
 * one (the rate box's star), else the cart list's. 0 when the picked list's price is not at
 * hand (a resumed held line): the box then sets no minimum and the server judges at checkout.
 */
export function lineListRate(cartListRate: number, pickedList: string | undefined, pickedListRate: number | undefined): number {
  return pickedList ? Number(pickedListRate) || 0 : cartListRate;
}

/**
 * The lowest rate the cashier may type in a line's rate box, or null when there is none: the
 * till must enforce the list floor and not allow discounts. `listRate` is the line's list price,
 * in the box's own tax terms. The server still judges every sale at checkout.
 * ponytail: ignores Pricing Rule promos the server would also accept; they apply without typing.
 */
export function rateBoxMinimum(
  posDetails: { powerpack_enforce_list_floor?: unknown; allow_discount_change?: unknown; powerpack_list_floor_discount_allowance?: unknown } | null | undefined,
  listRate: number,
): number | null {
  if (!Number(posDetails?.powerpack_enforce_list_floor) || Number(posDetails?.allow_discount_change) || !(listRate > 0)) return null;
  const allowance = Math.min(Math.max(Number(posDetails?.powerpack_list_floor_discount_allowance) || 0, 0), 100);
  return Math.round(listRate * (1 - allowance / 100) * 100) / 100;
}

/** A line's picked price list: the cart's per-line switch, else the list a held order's line came
 * back with (checkout opened from Invoice History has no cart switches, only the held lines). */
export function linePickedList(discountData: { selectedPriceList?: string }, item: object): string {
  return discountData.selectedPriceList || (item as { selected_price_list?: string }).selected_price_list || "";
}

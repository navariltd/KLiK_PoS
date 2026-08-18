/**
 * How an item's stock figure should read.
 *
 * When the backend could not read stock (no permission on Bin) every balance arrives as 0.
 * Rendering that as a confident "0" tells a cashier the shelf is empty when it may be full,
 * so unknown stock reads as an em dash instead. "None left" and "cannot tell" are different
 * facts and the till should not conflate them.
 */
export const UNKNOWN_AVAILABILITY = "—";

export function formatAvailability(
  available: number | string | null | undefined,
  stockUnavailable: boolean,
): string {
  if (stockUnavailable) {
    return UNKNOWN_AVAILABILITY;
  }
  if (available === null || available === undefined || available === "") {
    return UNKNOWN_AVAILABILITY;
  }
  return String(available);
}

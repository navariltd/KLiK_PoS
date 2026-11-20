/**
 * Which Invoice History tab a row belongs to. The list and the tab counts both use this, so
 * they can't disagree again (the count once had no rule for Queued and always showed 0).
 */
type TabRow = { status?: string; queueStatus?: string; isQuote?: boolean; isHeldOrder?: boolean };

// Queued, Processing and Failed are one thing to the reader: a sale the server has not
// finished with. The Queue Status column tells them apart.
const UNFINISHED = ["queued", "processing", "failed"];

const queueStatusOf = (row: TabRow) => (row.queueStatus || "").trim().toLowerCase();

export const isQueueFailed = (row: TabRow) => queueStatusOf(row) === "failed";

export function matchesInvoiceTab(row: TabRow, tab: string): boolean {
  if (tab === "Quotes") return Boolean(row.isQuote);
  // A held order's status reads as its approval state ("Approval pending"), not always "Held".
  if (tab === "Held" && row.isHeldOrder) return true;
  if (tab === "all") return true;
  if (tab === "queued") return UNFINISHED.includes(queueStatusOf(row));
  return (row.status || "").trim() === tab.trim();
}

/** Held and Quotes come from their own lists (held Sales Orders, Quotations); every other tab
 * from the invoices. */
export function rowsForTab<T>(tab: string, sources: { invoices: T[]; heldOrders: T[]; quotes: T[] }): T[] {
  if (tab === "Held") return sources.heldOrders;
  if (tab === "Quotes") return sources.quotes;
  return sources.invoices;
}

/** Only invoice tabs page through Sales Invoices; Held and Quotes load whole lists of their own. */
export const tabPagesInvoices = (tab: string) => tab !== "Held" && tab !== "Quotes";

/** Failed Queue was merged into Queued; links to it (dashboard alerts) land there. */
export const normalizeInvoiceTab = (tab: string) => (tab === "queue_failed" ? "queued" : tab);

export const sortFailedFirst = <T extends TabRow>(rows: T[]): T[] =>
  [...rows].sort((a, b) => Number(isQueueFailed(b)) - Number(isQueueFailed(a)));

export const countFailed = (rows: TabRow[]) => rows.filter(isQueueFailed).length;

export type QuoteState = "open" | "converted" | "closed";

/** The badge a quote row shows. "Quote" and "Expired" are what open quotes always showed. */
export function quoteStatusLabel(q: { state?: QuoteState; expired?: boolean; invoice?: string | null; status?: string; docstatus?: number }): string {
  if (q.state === "converted") return q.invoice ? "Converted" : "Ordered";
  if (q.state === "closed") return q.docstatus === 2 ? "Cancelled" : "Lost";
  return q.expired ? "Expired" : "Quote";
}

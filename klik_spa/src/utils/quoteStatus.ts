export type QuoteState = "open" | "converted" | "closed";

/** The badge a quote row shows, in ERPNext's Quotation status names. A quote sold straight to
 *  an invoice stays "Open" in ERPNext; the till shows it as Ordered. */
export function quoteStatusLabel(q: { state?: QuoteState; expired?: boolean; invoice?: string | null; status?: string; docstatus?: number }): string {
  if (q.state === "converted") return q.status === "Partially Ordered" ? q.status : "Ordered";
  if (q.state === "closed") return q.docstatus === 2 ? "Cancelled" : "Lost";
  return q.expired ? "Expired" : "Open";
}

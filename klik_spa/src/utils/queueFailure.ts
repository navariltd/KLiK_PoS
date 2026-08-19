/**
 * Formatting for background invoice-submission failures.
 *
 * Checkout is asynchronous: queue_sales_invoice returns HTTP 200 as soon as the invoice is
 * queued, and a worker submits it afterwards. When that submit fails the request is long
 * gone, so the cashier sees a completed sale. The backend records the reason on the invoice
 * and in a Notification Log, but neither is in front of someone standing at a counter.
 */

export interface QueueFailureEvent {
  invoice_name?: string;
  customer?: string;
  error?: string;
}

/** Human-readable one-liner for a failed queued invoice. */
export function formatQueueFailure(event: QueueFailureEvent | null | undefined): string {
  const invoice = event?.invoice_name?.trim();
  const customer = event?.customer?.trim();
  const reason = event?.error?.trim();

  const subject = invoice
    ? `Invoice ${invoice}${customer ? ` for ${customer}` : ""} was not submitted`
    : "An invoice was not submitted";

  return reason ? `${subject}: ${reason}` : `${subject}. No reason was recorded.`;
}

export interface UnresolvedQueueFailure {
  invoice_name: string;
  customer?: string;
  grand_total?: number;
  currency?: string;
  error?: string;
  attempts?: number;
  failed_at?: string | null;
}

/**
 * Headline for the unresolved-sales banner.
 *
 * Deliberately blunt about money: an unposted sale is a till that will not balance, and the
 * cashier needs to grasp that faster than they can read a list.
 */
export function summariseUnresolvedFailures(failures: UnresolvedQueueFailure[]): string {
  if (!failures.length) return "";

  const noun = failures.length === 1 ? "sale" : "sales";
  return `${failures.length} ${noun} did not post and ${failures.length === 1 ? "is" : "are"} not recorded yet.`;
}

/** One line per unresolved sale: who it was for, how much, and why it failed. */
export function describeUnresolvedFailure(failure: UnresolvedQueueFailure): string {
  const parts = [failure.invoice_name];
  if (failure.customer) parts.push(failure.customer);
  if (typeof failure.grand_total === "number" && failure.grand_total > 0) {
    parts.push(`${failure.currency ?? ""} ${failure.grand_total}`.trim());
  }
  const head = parts.join(" · ");
  return failure.error ? `${head} — ${failure.error}` : head;
}

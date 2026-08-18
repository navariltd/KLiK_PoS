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

import { useEffect } from "react";
import { toast } from "react-toastify";
import { formatQueueFailure, type QueueFailureEvent } from "../utils/queueFailure";

/** Matches the backend's QUEUE_FAILURE_EVENT in klik_pos/api/sales_invoice.py. */
export const QUEUE_FAILURE_EVENT = "klik_pos_invoice_queue_failed";

interface FrappeRealtimeClient {
  on?: (event: string, handler: (data: QueueFailureEvent) => void) => void;
  off?: (event: string, handler: (data: QueueFailureEvent) => void) => void;
}

/**
 * Surface background invoice-submission failures at the till.
 *
 * Mounted once at the app root rather than in the checkout dialog: the dialog has already
 * closed by the time a queued submit fails, and the cashier may have moved on to the next
 * sale. The toast does not auto-dismiss - an unposted sale is not something to notice or
 * miss within three seconds.
 */
export function useQueueFailureAlerts() {
  useEffect(() => {
    const realtime = (window as typeof window & { frappe?: { realtime?: FrappeRealtimeClient } })
      ?.frappe?.realtime;
    if (!realtime?.on) return;

    const handler = (data: QueueFailureEvent) => {
      toast.error(formatQueueFailure(data), { autoClose: false });
    };

    realtime.on(QUEUE_FAILURE_EVENT, handler);
    return () => {
      realtime.off?.(QUEUE_FAILURE_EVENT, handler);
    };
  }, []);
}

import { useCallback, useEffect, useState } from "react";
import type { UnresolvedQueueFailure } from "../utils/queueFailure";
import { QUEUE_FAILURE_EVENT } from "./useQueueFailureAlerts";

interface FrappeRealtimeClient {
  on?: (event: string, handler: () => void) => void;
  off?: (event: string, handler: () => void) => void;
}

/**
 * Sales that were queued, failed, and still have not posted.
 *
 * The realtime toast is the fast path and this is the durable one: it answers "is anything
 * still unposted?" at load, so a cashier who reloaded or was serving someone else when the
 * toast fired still finds out. Refetches when a new failure is announced, so the two stay in
 * step without the banner having to interpret the event payload.
 *
 * Fails silent — a status check must never be the thing that breaks the POS.
 */
export function useUnresolvedQueueFailures() {
  const [failures, setFailures] = useState<UnresolvedQueueFailure[]>([]);
  const [total, setTotal] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(
        "/api/method/klik_pos.api.sales_invoice.get_unresolved_queue_failures",
      );
      if (!response.ok) return;

      const data = await response.json();
      const message = data?.message;
      if (message?.success && Array.isArray(message.invoices)) {
        setFailures(message.invoices as UnresolvedQueueFailure[]);
        setTotal(typeof message.count === "number" ? message.count : message.invoices.length);
      }
    } catch {
      // Deliberately silent — see the note above.
    }
  }, []);

  useEffect(() => {
    void refresh();

    const realtime = (window as typeof window & { frappe?: { realtime?: FrappeRealtimeClient } })
      ?.frappe?.realtime;
    if (!realtime?.on) return;

    const handler = () => {
      void refresh();
    };
    realtime.on(QUEUE_FAILURE_EVENT, handler);
    return () => {
      realtime.off?.(QUEUE_FAILURE_EVENT, handler);
    };
  }, [refresh]);

  return { failures, total, refresh };
}

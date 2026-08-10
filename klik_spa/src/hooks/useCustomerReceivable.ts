import { useEffect, useState } from "react";
import { getCustomerReceivables, type CustomerReceivable } from "../services/paymentEntry";

/**
 * One customer's receivable position, fetched when a Receive modal opens for them.
 * Returns null while loading, on failure, or when that customer owes nothing — callers
 * treat all three the same way: open the modal without allocation targets.
 */
export function useCustomerReceivable(customer: string | null) {
  const [receivable, setReceivable] = useState<CustomerReceivable | null>(null);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  // Derived, not stored: a stored flag is false for one render after the customer changes,
  // and that single frame is enough for a gated modal to mount and unmount again.
  const isLoading = Boolean(customer) && loadedFor !== customer;

  useEffect(() => {
    if (!customer) {
      setReceivable(null);
      setLoadedFor(null);
      return;
    }
    let isCurrent = true;
    getCustomerReceivables({ customer })
      .then((response) => {
        if (!isCurrent) return;
        setReceivable(response.data?.[0] || null);
      })
      .catch(() => {
        // A failed lookup must not block taking the payment — fall back to on-account.
        if (isCurrent) setReceivable(null);
      })
      .finally(() => {
        if (isCurrent) setLoadedFor(customer);
      });

    return () => {
      isCurrent = false;
    };
  }, [customer]);

  return { receivable, isLoading };
}

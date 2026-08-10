import { useEffect, useState } from "react";
import { getCustomerAccountSummary, type CustomerAccountSummary } from "../services/customerService";

/**
 * One customer's account summary — the four headline figures shown on the customer detail
 * page cards. Sourced from a single server call so invoice count, net revenue, avg order
 * value, and outstanding balance can never disagree with each other or with the AR report.
 *
 * Returns null while loading, on failure, or before a customer id is known — callers must
 * render a dash in that case, never a stale or zero figure. `summary.outstanding` may itself
 * be null even on success (AR path could not determine it); that too must render as a dash,
 * distinct from a genuine 0.
 */
export function useCustomerSummary(customer: string | null) {
  const [summary, setSummary] = useState<CustomerAccountSummary | null>(null);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  // Derived, not stored: a stored flag is false for one render after the customer changes,
  // and that single frame is enough for the cards to flash a stale value.
  const isLoading = Boolean(customer) && loadedFor !== customer;

  useEffect(() => {
    // Clear on every customer change, not only when it goes falsy — otherwise a truthy id
    // swapped for a different truthy id leaves the previous customer's figures on screen
    // until the new fetch resolves, which is exactly the stale-figure this hook exists to
    // prevent.
    setSummary(null);
    setLoadedFor(null);

    if (!customer) {
      return;
    }
    let isCurrent = true;
    getCustomerAccountSummary(customer)
      .then((response) => {
        if (!isCurrent) return;
        setSummary(response);
      })
      .catch(() => {
        // A failed lookup must not render a confident wrong number — fall back to the dash.
        if (isCurrent) setSummary(null);
      })
      .finally(() => {
        if (isCurrent) setLoadedFor(customer);
      });

    return () => {
      isCurrent = false;
    };
  }, [customer]);

  return { summary, isLoading };
}

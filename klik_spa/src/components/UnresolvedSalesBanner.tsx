import { useState } from "react";
import { toast } from "react-toastify";
import { useUnresolvedQueueFailures } from "../hooks/useUnresolvedQueueFailures";
import { describeUnresolvedFailure, summariseUnresolvedFailures } from "../utils/queueFailure";

/**
 * Sales that never posted, shown until they do.
 *
 * Not dismissible, unlike the permission banner: an unposted sale is money the till will not
 * balance, and it clears itself the moment the invoice submits. Nagging is the correct
 * behaviour here — the condition that raises it is the condition that resolves it, so there
 * is no acknowledgement state to drift out of sync with reality.
 *
 * Collapsed to a single line by default though: persistent is not the same as overwhelming,
 * and a till carrying a dozen failures was pushing the entire POS off screen behind its own
 * error list.
 */
export default function UnresolvedSalesBanner() {
  const { failures, total, refresh } = useUnresolvedQueueFailures();
  const [retrying, setRetrying] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const headline = summariseUnresolvedFailures(failures, total);
  if (!headline) return null;

  const retry = async (invoiceName: string) => {
    setRetrying(invoiceName);
    try {
      const response = await fetch(
        "/api/method/klik_pos.api.sales_invoice.retry_failed_sales_invoice",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Frappe-CSRF-Token": window.csrf_token,
          },
          body: JSON.stringify({ invoice_name: invoiceName }),
          credentials: "include",
        },
      );
      const data = await response.json();
      if (!response.ok || data?.message?.success === false) {
        toast.error(data?.message?.message || `Could not retry ${invoiceName}.`);
      } else {
        toast.success(`${invoiceName} queued again.`);
      }
    } catch {
      toast.error(`Could not retry ${invoiceName}.`);
    } finally {
      setRetrying(null);
      void refresh();
    }
  };

  return (
    // lg:ml-20 clears the fixed w-20 sidebar, matching every other page shell. Without it the
    // banner slides underneath and the invoice numbers are chopped off at the left edge.
    <div
      role="alert"
      className="lg:ml-20 border-b border-red-200 bg-red-50 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
    >
      <div className="flex items-center gap-3 px-4 py-2">
        <span className="flex-1 font-medium">{headline}</span>
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          className="shrink-0 font-medium underline underline-offset-2"
          aria-expanded={expanded}
        >
          {expanded ? "Hide" : `Show ${failures.length}`}
        </button>
      </div>

      {/* Collapsed by default, and capped when open: a till with a long backlog was burying
          the entire POS behind its own error list. */}
      {expanded && (
        <ul className="max-h-44 space-y-1 overflow-y-auto px-4 pb-2">
          {failures.map((failure) => (
            <li key={failure.invoice_name} className="flex items-start gap-3">
              <span className="flex-1 break-words">{describeUnresolvedFailure(failure)}</span>
              <button
                type="button"
                onClick={() => retry(failure.invoice_name)}
                disabled={retrying === failure.invoice_name}
                className="shrink-0 font-medium underline underline-offset-2 disabled:opacity-50"
              >
                {retrying === failure.invoice_name ? "Retrying…" : "Retry"}
              </button>
            </li>
          ))}
          {total > failures.length && (
            <li className="pt-1 opacity-80">
              Showing the {failures.length} most recent of {total}. See the invoice list for the rest.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

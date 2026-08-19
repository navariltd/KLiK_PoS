import { useState } from "react";
import { usePermissionHealth } from "../hooks/usePermissionHealth";
import { summarisePermissionHealth } from "../utils/permissionHealth";

/**
 * One POS-wide banner naming what the current role cannot reach.
 *
 * klik_pos degrades rather than erroring on a permission gap, so without this the first sign
 * is a wrong number a cashier has already acted on. Checking up front covers every endpoint at
 * once and warns before the mistake instead of after.
 *
 * Dismissible for the session: it should be impossible to miss, but not impossible to work
 * past — the POS still functions in the degraded case.
 */
export default function PermissionHealthBanner() {
  const health = usePermissionHealth();
  const [dismissed, setDismissed] = useState(false);

  const message = summarisePermissionHealth(health);
  if (!message || dismissed) return null;

  const critical = !!health?.has_critical;

  return (
    <div
      role="status"
      className={`lg:ml-20 flex items-start gap-3 px-4 py-2 text-sm ${
        critical
          ? "bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-200"
          : "bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
      }`}
    >
      <span className="flex-1">{message}</span>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="shrink-0 font-medium underline underline-offset-2 opacity-80 hover:opacity-100"
        aria-label="Dismiss permission warning"
      >
        Dismiss
      </button>
    </div>
  );
}

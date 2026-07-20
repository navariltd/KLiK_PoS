import { toast } from "react-toastify";

/**
 * Enqueue a QZ (thermal) reprint of a document through cecypo_qz_extension's
 * print queue. Mirrors services/salesInvoice.ts's fetch+CSRF pattern.
 *
 * The endpoint lives in a separate app (cecypo_qz_extension). If that app is
 * not installed the call 404s — callers only render the button when the
 * POS-Profile-driven `custom_enable_qz_print` flag is on, which is itself
 * only present when that app is installed, so a 404 here is an unexpected
 * misconfiguration worth surfacing.
 */
export async function qzReprint(
  referenceName: string,
  referenceDoctype: string = "Sales Invoice",
): Promise<{ jobCount: number }> {
  const csrfToken = window.csrf_token;

  const response = await fetch(
    "/api/method/cecypo_qz_extension.api.queue.reprint",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Frappe-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        reference_doctype: referenceDoctype,
        reference_name: referenceName,
      }),
      credentials: "include",
    },
  );

  const result = await response.json();

  if (!response.ok) {
    // Frappe puts throw() messages in _server_messages; surface a concise one.
    const serverMsg = (() => {
      try {
        return JSON.parse(result._server_messages || "[]")
          .map((m: string) => JSON.parse(m).message)
          .join(" ");
      } catch {
        return "";
      }
    })();
    const msg = serverMsg || result.exception || "QZ print failed";
    toast.error(msg);
    throw new Error(msg);
  }

  const jobs = Array.isArray(result.message) ? result.message : [];
  if (jobs.length === 0) {
    toast.warn("No print route matched — nothing was sent to a printer.");
  } else {
    toast.success("Sent to printer");
  }
  return { jobCount: jobs.length };
}

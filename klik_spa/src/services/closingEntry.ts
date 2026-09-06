import { useState } from "react";

// HOOK: Create POS Closing Entry
interface ClosingBalance {
  mode_of_payment: string;
  closing_amount: number;
}

interface UseCreateClosingReturn {
  createClosingEntry: (closingBalance: ClosingBalance[]) => Promise<void>;
  isCreating: boolean;
  error: string | null;
  success: boolean;
}

export function useCreatePOSClosingEntry(): UseCreateClosingReturn {
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const createClosingEntry = async (closingBalance: ClosingBalance[]) => {
    setIsCreating(true);
    setError(null);
    setSuccess(false);
    const csrfToken = window.csrf_token;

    try {
      // console.log('Creating closing entry with:', closingBalance);

      const res = await fetch("/api/method/klik_pos.api.pos_entry.create_closing_entry", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Frappe-CSRF-Token": csrfToken,
          "Accept": "application/json",
        },
        body: JSON.stringify({ closing_balance: closingBalance }),
        credentials: "include",
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }

      const data = await res.json();

      if (data.message) {
        setSuccess(true);
      } else {
        throw new Error(data._server_messages || "Failed to create closing entry");
      }
        //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (err: any) {
      console.error("Error creating POS Closing Entry:", err);
      setError(err.message || "Unexpected error occurred");
    } finally {
      setIsCreating(false);
    }
  };

  return {
    createClosingEntry,
    isCreating,
    error,
    success,
  };
}

// Bulk-delete every Draft Sales Invoice left over from the current session, on
// explicit request (the "Delete all drafts" banner action on the Closing Shift
// screen). Backs klik_pos.api.pos_entry.delete_all_draft_invoices_for_current_session,
// which reuses the same routine already used by the POS Profile's
// "Clear draft invoices on close" setting -- this is just an opt-in,
// visible-count version of the same cleanup, triggered by the cashier instead of
// happening silently.
export async function bulkDeleteDraftInvoicesForCurrentSession(): Promise<{
  success: boolean;
  deleted_count?: number;
  message?: string;
  error?: string;
}> {
  const csrfToken = window.csrf_token;

  const res = await fetch(
    "/api/method/klik_pos.api.pos_entry.delete_all_draft_invoices_for_current_session",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Frappe-CSRF-Token": csrfToken,
        "Accept": "application/json",
      },
      credentials: "include",
    }
  );

  const data = await res.json();
  const result = data.message;

  if (!res.ok || !result || result.success === false) {
    const serverMsg = data._server_messages
      ? JSON.parse(data._server_messages)[0]
      : result?.error || "Failed to delete draft invoices";
    throw new Error(serverMsg);
  }

  return result;
}
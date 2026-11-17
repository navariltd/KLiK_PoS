const csrf = () => (window as { csrf_token?: string }).csrf_token as string;

const serverMessage = (result: { _server_messages?: string } | null) => {
  try {
    return JSON.parse(JSON.parse(result?._server_messages || "[]")[0]).message as string | undefined;
  } catch {
    return undefined;
  }
};

/** Save the cart as a submitted Quotation; takes the held-order payload. Never throws. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function createQuote(data: any): Promise<{ success: boolean; quote_name?: string; message?: string }> {
  try {
    const response = await fetch("/api/method/klik_pos.api.quotation.create_quote", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": csrf() },
      body: JSON.stringify({ data }),
      credentials: "include",
    });
    const result = await response.json().catch(() => null);
    if (response.ok && result?.message?.success) return result.message;
    return {
      success: false,
      message: result?.message?.message || serverMessage(result) || "Failed to save the quote",
    };
  } catch (error) {
    return { success: false, message: error instanceof Error ? error.message : "Failed to save the quote" };
  }
}

export interface QuoteListRow {
  name: string;
  customer: string;
  customer_name: string;
  walkin_name: string | null;
  transaction_date: string;
  valid_till: string | null;
  grand_total: number;
  currency: string;
  expired: boolean;
  /** open: loadable for sale; converted: invoiced or ordered; closed: Lost or cancelled. */
  state: "open" | "converted" | "closed";
  /** The invoice a converted quote became. */
  invoice: string | null;
  status: string;
  docstatus: number;
}

export async function getQuotes(params: { limit?: number; start?: number; search?: string; state?: "open" | "converted" | "closed" | "all" } = {}) {
  const qs = new URLSearchParams({
    limit: String(params.limit ?? 50),
    start: String(params.start ?? 0),
    search: params.search ?? "",
    state: params.state ?? "open",
  }).toString();
  return getMessage(`/api/method/klik_pos.api.quotation.get_quotes?${qs}`, "Failed to load quotes") as Promise<{
    success: boolean;
    data?: QuoteListRow[];
    message?: string;
  }>;
}

/** A GET's `message`, or {success: false, message} for a refusal or an unreachable server. Never throws. */
async function getMessage(url: string, fallback: string) {
  try {
    const response = await fetch(url, { credentials: "include" });
    const result = await response.json().catch(() => null);
    if (response.ok && result?.message) return result.message;
    return { success: false, message: result?.message?.message || serverMessage(result) || fallback };
  } catch (error) {
    return { success: false, message: error instanceof Error ? error.message : fallback };
  }
}

/** Same shape as getHeldOrderDetails, plus whether the quote is past its valid-till date. */
/** asCopy: a converted or closed quote too, as a new sale's lines at today's prices. */
export async function getQuoteDetails(quoteId: string, asCopy = false) {
  return getMessage(
    `/api/method/klik_pos.api.quotation.get_quote_details?quote_id=${encodeURIComponent(quoteId)}${asCopy ? "&as_copy=1" : ""}`,
    "Failed to load the quote",
  ) as Promise<{
    success: boolean;
    name: string;
    customer: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    customer_data?: any;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    items: any[];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    cart_meta: any;
    grand_total: number;
    currency: string;
    expired: boolean;
    message?: string;
  }>;
}

/** The quote's contact and customer email addresses, to prefill the email box. [] on failure. */
export async function getQuoteEmails(quoteName: string): Promise<string[]> {
  const res = await getMessage(
    `/api/method/klik_pos.api.quotation.get_quote_emails?quote_name=${encodeURIComponent(quoteName)}`,
    "",
  );
  return Array.isArray(res) ? res : [];
}

/** Email the quote PDF to comma-separated addresses. Throws the server's reason on failure. */
export async function sendQuoteEmail(quoteName: string, emails: string): Promise<void> {
  const response = await fetch("/api/method/klik_pos.api.quotation.send_quote_email", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": csrf() },
    body: JSON.stringify({ quote_name: quoteName, emails }),
    credentials: "include",
  });
  if (response.ok) return;
  const result = await response.json().catch(() => null);
  throw new Error(serverMessage(result) || "Failed to send the email");
}

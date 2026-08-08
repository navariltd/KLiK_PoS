import { extractErrorMessage } from "../utils/errorExtraction";

const BASE = "/api/method/klik_pos.api.statement_of_accounts";

export interface StatementTemplate {
  name: string;
  report: string;
  print_format?: string | null;
  letter_head?: string | null;
  modified?: string;
}

export interface StatementArgs {
  customer: string;
  company: string;
  template: string;
  as_of_date: string;
}

async function post(method: string, body: object) {
  const response = await fetch(`${BASE}.${method}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Frappe-CSRF-Token": window.csrf_token,
    },
    body: JSON.stringify(body),
    credentials: "include",
  });

  const result = await response.json();
  if (!response.ok) {
    throw new Error(extractErrorMessage(result, "Statement request failed"));
  }
  return result.message;
}

/** Whether this site has the app that provides statements. Never throws — a failure means no. */
export async function isStatementAvailable(): Promise<boolean> {
  try {
    const response = await fetch(`${BASE}.is_available`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
    });
    const result = await response.json();
    return Boolean(result?.message?.available);
  } catch {
    return false;
  }
}

export async function getStatementTemplates(company: string): Promise<StatementTemplate[]> {
  return (await post("get_statement_templates", { company })) || [];
}

export async function getStatementRecipient(customer: string): Promise<string> {
  return (await post("get_default_recipient", { party_type: "customer", party: customer })) || "";
}

export async function renderStatementHtml(args: StatementArgs): Promise<string> {
  return (await post("render_statement_html", args)) || "";
}

export async function emailStatement(
  args: StatementArgs & { recipient: string; cc?: string; bcc?: string }
): Promise<boolean> {
  return Boolean(await post("email_statement", args));
}

export interface BulkStatementRow {
  customer: string;
  customer_name: string;
  recipient?: string;
}

export interface BulkStatementPreview {
  will_send: BulkStatementRow[];
  no_email: BulkStatementRow[];
  not_permitted: number;
  no_transactions: number;
  total_customers: number;
}

export async function previewBulkStatements(
  company: string,
  template: string,
  asOfDate: string
): Promise<BulkStatementPreview> {
  return post("preview_bulk_statements", { company, template, as_of_date: asOfDate });
}

export async function emailBulkStatements(
  company: string,
  template: string,
  asOfDate: string
): Promise<{ queued: number }> {
  return post("email_bulk_statements", { company, template, as_of_date: asOfDate });
}

/**
 * Download the PDF.
 *
 * The endpoint sets frappe.local.response.type = "download", so a successful response body is
 * raw PDF bytes rather than JSON. A FAILED one is still JSON, so the content type has to be
 * checked before treating the body as a file — otherwise an error is saved as a corrupt PDF.
 */
export async function downloadStatement(args: StatementArgs): Promise<void> {
  const response = await fetch(`${BASE}.download_statement`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Frappe-CSRF-Token": window.csrf_token,
    },
    body: JSON.stringify(args),
    credentials: "include",
  });

  const contentType = response.headers.get("content-type") || "";
  if (!response.ok || contentType.includes("application/json")) {
    const result = await response.json().catch(() => null);
    throw new Error(extractErrorMessage(result, "Failed to download the statement"));
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filenameFromResponse(response) || `Statement-${args.customer}.pdf`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

function filenameFromResponse(response: Response): string {
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  return match?.[1] ? decodeURIComponent(match[1]) : "";
}

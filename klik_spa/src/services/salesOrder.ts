import type { MpesaPushInfo } from './mpesaOrder';

const csrf = () => (window as { csrf_token?: string }).csrf_token as string;

/** The held order being finished was checked out, cleared or completed elsewhere. */
export class HeldOrderGoneError extends Error {
  constructor(message: string, public readonly orderId: string) {
    super(message);
    this.name = 'HeldOrderGoneError';
  }
}

async function apiPost(endpoint: string, body: object) {
  const response = await fetch(`/api/method/${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Frappe-CSRF-Token': csrf(),
    },
    body: JSON.stringify(body),
    credentials: 'include',
  });
  const result = await response.json();
  if (result.message?.code === 'held_order_gone') {
    throw new HeldOrderGoneError(result.message.message, result.message.order_id);
  }
  if (!response.ok || result.message?.success === false) {
    const msg = result.message?.message || result.message?.error || result._server_messages || 'Request failed';
    // The server answered, so a checkout it refused created nothing to recover. Unmarked, the
    // payment dialog keeps the attempt pending and the cart's recovery poll holds Checkout on
    // "Checking cart..." for up to 30s. Same contract as createSalesInvoice.
    throw Object.assign(new Error(msg), { checkoutResponseReceived: true, checkoutResponse: result.message });
  }
  return result.message;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function createHeldOrder(data: any) {
  return apiPost('klik_pos.api.sales_order.create_held_order', { data }) as Promise<{
    success: boolean;
    order_name?: string;
    approval_requested?: boolean;
    approval_state?: string | null;
    price_breach?: number;
  }>;
}

export async function getHeldOrderDetails(orderId: string) {
  const response = await fetch(
    `/api/method/klik_pos.api.sales_order.get_held_order_details?order_id=${encodeURIComponent(orderId)}`,
    { credentials: 'include' },
  );
  const result = await response.json();
  return result.message as {
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
    message?: string;
    approval_state?: string | null;
    price_breach?: number;
    /** An M-Pesa order kept for its push, and that push. */
    mpesa_order?: number;
    mpesa_request?: MpesaPushInfo | null;
  };
}

export async function getHeldOrderActions(orderId: string) {
  const response = await fetch(
    `/api/method/klik_pos.api.sales_order.get_held_order_actions?order_id=${encodeURIComponent(orderId)}`,
    { credentials: 'include' },
  );
  const result = await response.json();
  return result.message as {
    success: boolean;
    actions?: string[];
    approval_state?: string | null;
    price_breach?: number;
    code?: string;
    message?: string;
  };
}

export async function applyHeldOrderAction(orderId: string, action: string) {
  return apiPost('klik_pos.api.sales_order.apply_held_order_action', { order_id: orderId, action }) as Promise<{
    success: boolean;
    approval_state?: string | null;
    price_breach?: number;
  }>;
}

export interface HeldOrderShareTools {
  /** The site's "Copy as Message" Client Script for Sales Order, or null. */
  copy_message_script: string | null;
  /** Whether PowerPack's Copy as Image is installed and on. */
  copy_image: boolean;
  /** The site's date format, for the script's dates. */
  date_format: string | null;
}

export async function getHeldOrderShareTools(doctype = 'Sales Order'): Promise<HeldOrderShareTools> {
  const response = await fetch(`/api/method/klik_pos.api.sales_order.get_held_order_share_tools?doctype=${encodeURIComponent(doctype)}`, {
    credentials: 'include',
  });
  const none = { copy_message_script: null, copy_image: false, date_format: null };
  if (!response.ok) return none;
  const result = await response.json();
  return {
    copy_message_script: result.message?.copy_message_script ?? null,
    copy_image: !!result.message?.copy_image,
    date_format: result.message?.date_format ?? null,
  };
}

/** The held order's Sales Order document, as the desk form has it, and its currency symbol. */
export async function getHeldOrderShareDoc(orderId: string, doctype = 'Sales Order'): Promise<{
  doc: Record<string, unknown> & { doctype: string };
  currency_symbol: string;
}> {
  const response = await fetch(
    `/api/method/klik_pos.api.sales_order.get_held_order_share_doc?order_id=${encodeURIComponent(orderId)}&doctype=${encodeURIComponent(doctype)}`,
    { credentials: 'include' },
  );
  const result = await response.json();
  if (!response.ok || !result.message?.doc) throw new Error(`Could not load ${orderId}`);
  return result.message;
}

/** frappe.call for a desk script: POST, resolving to the response's message. */
export async function callMethod(method: string, args: Record<string, unknown>) {
  const response = await fetch(`/api/method/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': csrf() },
    body: JSON.stringify(args),
    credentials: 'include',
  });
  const result = await response.json();
  if (!response.ok) {
    const messages = JSON.parse(result._server_messages || '[]').map((m: string) => JSON.parse(m).message);
    throw new Error(messages.join(' ') || result.exc_type || 'Request failed');
  }
  return result.message;
}

export async function deleteHeldOrder(orderId: string) {
  return apiPost('klik_pos.api.sales_order.delete_held_order', { order_id: orderId });
}

export async function getHeldOrders(
  params: { limit?: number; start?: number; search?: string; skipOpeningEntryFilter?: boolean } = {},
) {
  const qs = new URLSearchParams({
    limit: String(params.limit ?? 50),
    start: String(params.start ?? 0),
    search: params.search ?? '',
    skip_opening_entry_filter: params.skipOpeningEntryFilter ? 'true' : 'false',
  }).toString();
  const response = await fetch(
    `/api/method/klik_pos.api.sales_order.get_held_orders?${qs}`,
    { credentials: 'include' },
  );
  const result = await response.json();
  return result.message as {
    success: boolean;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    data: any[];
    total_count: number;
    error?: string;
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function checkoutHeldOrder(orderId: string, data: any) {
  return apiPost('klik_pos.api.sales_order.checkout_held_order', { order_id: orderId, data });
}

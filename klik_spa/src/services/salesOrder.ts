const csrf = () => (window as { csrf_token?: string }).csrf_token as string;

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
  if (!response.ok || result.message?.success === false) {
    const msg = result.message?.message || result.message?.error || result._server_messages || 'Request failed';
    throw new Error(msg);
  }
  return result.message;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function createHeldOrder(data: any) {
  return apiPost('klik_pos.api.sales_order.create_held_order', { data });
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
  };
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

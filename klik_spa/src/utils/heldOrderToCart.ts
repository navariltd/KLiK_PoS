import { getHeldOrderDetails } from '../services/salesOrder';
import { cacheHeldOrder, getOriginalDraftInvoiceId, getOriginalHeldOrderId, getOriginalQuotationId, loadCachedItemsToCart, markCartAlreadyLoaded, setCachedItems } from './draftInvoiceCache';
import { mergeCartLines, mergedOrderIds } from './mergeHeldOrder';
import { PRICE_APPROVAL_PENDING } from './priceApproval';
import { transformCustomerInfo } from './transformCustomerInfo';
import { useCartStore } from '../stores/cartStore';
import { usePOSProfileStore } from '../stores/posProfileStore';
import { checkoutExtrasFromHeldOrder, heldItemsToCart, tillFlags } from './heldOrderPayload';
import { toast } from 'react-toastify';
import { losToast } from './lossOfSale';
import type { CartItem, Customer } from '../../types';

// transformCustomerInfo returns the `types/customer` Customer; the cache stores the
// `types/index` Customer. They are structurally compatible at runtime — bridge here.
type CacheCustomer = Customer;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function fetchHeldOrder(orderId: string): Promise<any> {
  const orderData = await getHeldOrderDetails(orderId);
  if (!orderData?.success) {
    throw new Error(orderData?.message || 'Failed to load held order');
  }
  return orderData;
}

/** Why the cart can't take this order's lines by merging, or null when it can. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function mergeRefusal(orderId: string, orderData: any): string | null {
  if (getOriginalDraftInvoiceId()) return 'The cart is a draft invoice: it can only be replaced.';
  if (getOriginalQuotationId()) return 'The cart is a quote: it can only be replaced.';
  if (mergedOrderIds(useCartStore.getState().cartItems, null).includes(orderId)) return `${orderId} is already merged into the cart.`;
  if (orderData.mpesa_order) return `${orderId} is an M-Pesa order: it can only be opened on its own.`;
  if (orderData.approval_state === PRICE_APPROVAL_PENDING) return `${orderId} is waiting for price approval: it can only be opened on its own.`;
  return null;
}

/**
 * Add a held order's lines to the cart. The cart keeps its own customer and checkout details;
 * the merged order is deleted when the sale is held again or checked out. A cart with no held
 * order behind it becomes this order, so a hold updates it instead of making another.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function mergeHeldOrderIntoCart(orderId: string, orderData: any, combine: boolean): void {
  const cart = useCartStore.getState();
  const items = mergeCartLines(cart.cartItems, heldItemsToCart(orderData.items), orderId, combine);
  const customer = (cart.selectedCustomer ?? orderData.customer_data ?? null) as CacheCustomer | null;
  if (getOriginalHeldOrderId()) {
    setCachedItems(items, customer);
  } else {
    // No approval or M-Pesa state carried: they were given for the order's lines alone.
    cacheHeldOrder(orderId, items, customer);
  }
  markCartAlreadyLoaded();
  useCartStore.setState({ cartItems: items, selectedCustomer: customer as never });

  const dropped: string[] = [];
  if (cart.selectedCustomer && orderData.customer && orderData.customer !== cart.selectedCustomer.id) dropped.push(`its customer (${orderData.customer})`);
  if (Number(orderData.discount_amount) > 0) dropped.push('its order discount');
  if (Number(orderData.cart_meta?.deliveryCharge) > 0) dropped.push('its delivery charge');
  toast.success(`${orderId} merged into the cart.`);
  if (dropped.length) toast.info(`Not carried over from ${orderId}: ${dropped.join(', ')}.`);
}

export async function addHeldOrderToCart(orderId: string): Promise<boolean> {
  const orderData = await fetchHeldOrder(orderId);
  return fillCartFromOrder(orderData, (items, customer) => cacheHeldOrder(
    orderId,
    items,
    customer,
    Number(orderData.discount_amount) || 0,
    {
      state: orderData.approval_state ?? null,
      priceBreach: Number(orderData.price_breach) || 0,
    },
    { isMpesaOrder: Boolean(orderData.mpesa_order), request: orderData.mpesa_request ?? null },
  ));
}

/**
 * Fill the cart from a held order's or a quote's details (get_held_order_details shape).
 * `cache` records which document the cart now stands for.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function fillCartFromOrder(orderData: any, cache: (items: CartItem[], customer: CacheCustomer | null) => void): Promise<boolean> {
  const items = heldItemsToCart(orderData.items);
  // The server split each line's full ask against the stock at this till now.
  for (const item of items) {
    if ((item.los_qty ?? 0) > 0) {
      toast.warning(losToast(item.name, item.uom, { quantity: item.quantity, los_qty: item.los_qty ?? 0 }));
    }
  }


  // Resolve customer object — must be a full Customer (with `id`) so checkout can
  // send customer.id, matching the customer-search selection flow.
  let customer: CacheCustomer | null = null;

  // Fast path: the full customer object was persisted in cart_meta at hold time.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (orderData.customer_data && (orderData.customer_data as any).id) {
    customer = orderData.customer_data as unknown as CacheCustomer;
  } else if (orderData.customer) {
    // Fallback (older held orders): re-fetch and transform by customer id.
    try {
      const res = await fetch(
        `/api/method/klik_pos.api.customer.get_customer_info?customer_name=${encodeURIComponent(orderData.customer)}`,
        { credentials: 'include' },
      );
      const data = await res.json();
      const raw = data?.message;
      if (raw && raw.name && raw.success !== false) {
        customer = transformCustomerInfo(raw) as unknown as CacheCustomer;
      }
    } catch {
      // non-fatal — cart will load without a resolved customer object
    }
  }

  cache(items, customer);

  // Restore per-transaction walk-in details (name/tax_id/phone) into the cart store.
  // Also recovers tax_id, which was previously dropped from the UI on resume.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const od = orderData as any;
  useCartStore.getState().setWalkinDetails({
    name: od.walkin_name || '',
    taxId: od.cart_meta?.tax_id || od.tax_id || '',
    phone: od.walkin_phone || '',
  });

  useCartStore.getState().setExtraFields(od.extra_fields || od.cart_meta?.extra_fields || {});
  // The till's switches decide what comes back: a rule, charge or discount it no longer
  // allows would only get the next hold or checkout refused.
  const flags = tillFlags(usePOSProfileStore.getState().posDetails as Record<string, unknown> | null);
  useCartStore.getState().setShippingRule(
    flags.shippingRule ? (od.shipping_rule || od.cart_meta?.shipping_rule || null) : null,
  );
  useCartStore.getState().setCheckoutExtras(checkoutExtrasFromHeldOrder(od, flags));

  const loaded = await loadCachedItemsToCart();
  // The list the cart was on when held. Set directly, not through setSelectedPriceList:
  // that re-prices the cart and would overwrite the held rates.
  if (od.selling_price_list) useCartStore.setState({ selectedPriceList: od.selling_price_list });
  return loaded;
}

import { EMPTY_CHECKOUT_EXTRAS } from './heldOrderPayload';
import { useCartStore } from '../stores/cartStore';
import { usePOSProfileStore } from '../stores/posProfileStore';
import { defaultCreditSale } from './saleType';
import type { CartItem, Customer } from '../../types';
import type { MpesaPushInfo } from '../services/mpesaOrder';

interface DraftInvoiceCache {
  items: CartItem[];
  timestamp: number;
  invoiceId: string;
  customer: Customer | null;
  originalDraftInvoiceId: string; // draft SI (M-Pesa / legacy held)
  originalHeldOrderId?: string;   // SO-based held order
  originalQuotationId?: string;   // the quote this sale was loaded from
  originalHeldOrderApprovalState?: string | null; // price approval workflow_state as loaded
  originalHeldOrderPriceBreach?: number;          // price breach flag as loaded
  originalHeldOrderIsMpesa?: boolean;             // an M-Pesa order kept for its push
  originalHeldOrderMpesaRequest?: MpesaPushInfo | null; // that push, as loaded
  orderDiscountAmount?: number;   // additional discount amount carried over from hold/draft
  remarks?: string;               // a recalled draft invoice's own remarks
}

const CACHE_KEY = 'draft-invoice-cache';

/**
 * How long a cached *cart* may be restored for. A cart abandoned half an hour ago should
 * not reappear under someone else's sale, so restoring items stays on a timer.
 */
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes

export function cacheDraftInvoiceItems(
  invoiceId: string,
  items: CartItem[],
  customer: Customer | null,
  orderDiscountAmount = 0,
  remarks = "",
): void {
  const cache: DraftInvoiceCache = {
    items,
    timestamp: Date.now(),
    invoiceId,
    customer,
    originalDraftInvoiceId: invoiceId,
    orderDiscountAmount,
    remarks,
  };

  localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
}

export function cacheHeldOrder(
  orderId: string,
  items: CartItem[],
  customer: Customer | null,
  orderDiscountAmount = 0,
  approval?: { state?: string | null; priceBreach?: number },
  mpesa?: { isMpesaOrder: boolean; request: MpesaPushInfo | null },
): void {
  const cache: DraftInvoiceCache = {
    items,
    timestamp: Date.now(),
    invoiceId: orderId,
    customer,
    originalDraftInvoiceId: '',
    originalHeldOrderId: orderId,
    originalHeldOrderApprovalState: approval?.state ?? null,
    originalHeldOrderPriceBreach: approval?.priceBreach ?? 0,
    originalHeldOrderIsMpesa: Boolean(mpesa?.isMpesaOrder),
    originalHeldOrderMpesaRequest: mpesa?.request ?? null,
    orderDiscountAmount,
  };
  localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
}

/** A quote loaded for sale: no held order or draft behind it, just the link checkout sends. */
export function cacheQuote(
  quoteId: string,
  items: CartItem[],
  customer: Customer | null,
  orderDiscountAmount = 0,
): void {
  const cache: DraftInvoiceCache = {
    items,
    timestamp: Date.now(),
    invoiceId: quoteId,
    customer,
    originalDraftInvoiceId: '',
    originalQuotationId: quoteId,
    orderDiscountAmount,
  };
  localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
}

/**
 * Which document this cart is editing, regardless of how long the edit has taken.
 *
 * The identity deliberately does NOT expire. It used to be read through the five-minute
 * items cache, so recalling a held order, serving somebody else, and holding it again
 * twenty minutes later sent no held_order_id at all - and the server, asked to hold an
 * order it had never seen, inserted a second Sales Order and left the first one held. The
 * duplicate only appeared when the edit ran long, which is why it looked intermittent.
 *
 * The link dies with the cart, not with a stopwatch: clearCart, checkout and hold all call
 * clearDraftInvoiceCache.
 */
function readCacheIgnoringAge(): DraftInvoiceCache | null {
  try {
    const cached = localStorage.getItem(CACHE_KEY);
    if (!cached) return null;
    return JSON.parse(cached) as DraftInvoiceCache;
  } catch {
    return null;
  }
}

export function getCachedDraftInvoiceItems(): DraftInvoiceCache | null {
  try {
    const cached = localStorage.getItem(CACHE_KEY);

    if (!cached) {
      return null;
    }

    const cache: DraftInvoiceCache = JSON.parse(cached);

    const now = Date.now();
    const age = now - cache.timestamp;

    if (age > CACHE_DURATION) {
      clearDraftInvoiceCache();
      return null;
    }

    return cache;
  } catch (error) {
    console.error('Error retrieving cached draft invoice items:', error);
    clearDraftInvoiceCache();
    return null;
  }
}

/**
 * Drop the link to a draft that is no longer one (submitted or cancelled elsewhere) while
 * keeping the cart, so the sale can go through as a new invoice.
 */
export function forgetOriginalDraftInvoice(): void {
  const cache = readCacheIgnoringAge();
  if (!cache) return;
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ ...cache, originalDraftInvoiceId: '' }));
  } catch {
    // Storage refused (quota, private mode): the link stays and the refusal repeats, which
    // is annoying but safe.
  }
}

/** The same for a held order that is gone: drop the link, keep the cart. */
export function forgetOriginalHeldOrder(): void {
  const cache = readCacheIgnoringAge();
  if (!cache) return;
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ ...cache, originalHeldOrderId: '' }));
  } catch {
    // As above: the refusal repeats, which is safe.
  }
}

export function clearDraftInvoiceCache(): void {
  localStorage.removeItem(CACHE_KEY);
}

export async function loadCachedItemsToCart(): Promise<boolean> {
  const cachedData = getCachedDraftInvoiceItems();
  if (!cachedData || cachedData.items.length === 0) {
    return false;
  }

  const mappedItems = cachedData.items.map((item) => ({
    ...item,
    item_code: item.item_code || item.id,
    quantity: item.quantity,
    bundle_entries: item.bundle_entries || [],
  }));

  useCartStore.setState((state) => ({
    ...state,
    cartItems: mappedItems,
    appliedCoupons: [],
    selectedCustomer: cachedData.customer,
    // A resumed sale starts from the till's default for its own customer, never from the last cart's pick.
    creditSale: defaultCreditSale(usePOSProfileStore.getState().posDetails, cachedData.customer),
    // A recalled draft invoice brings its own discount and nothing else of checkout's: the
    // last sale's delivery charge, person and tax template must not ride along onto it. A
    // held order sets its own checkout state just before this, so it is left alone.
    ...(cachedData.originalDraftInvoiceId
      ? {
          checkoutExtras: {
            ...EMPTY_CHECKOUT_EXTRAS,
            orderDiscountAmount: Number(cachedData.orderDiscountAmount) || 0,
            remarks: cachedData.remarks || "",
          },
        }
      : {}),
  }));

  return true;
}

export function hasCachedDraftInvoiceItems(): boolean {
  const cached = localStorage.getItem(CACHE_KEY);

  if (!cached) {
    return false;
  }

  try {
    const cache: DraftInvoiceCache = JSON.parse(cached);
    const isValid = Date.now() - cache.timestamp <= CACHE_DURATION;
    return isValid;
  } catch (error) {
    console.error('Error checking cache validity:', error);
    return false;
  }
}

/** A merge changed the cart: a page reload restores from the cache, so it must carry the cart as it stands. */
export function setCachedItems(items: CartItem[], customer: Customer | null): void {
  const cache = readCacheIgnoringAge();
  if (cache) localStorage.setItem(CACHE_KEY, JSON.stringify({ ...cache, items, customer, timestamp: Date.now() }));
}

// The cart already stands for the cache (a merge set both): POS must not reload it on mount,
// which would also clear coupons and reset the sale type.
let cartAlreadyLoaded = false;
export function markCartAlreadyLoaded(): void {
  cartAlreadyLoaded = true;
}
/** Read once by POS on mount. */
export function takeCartAlreadyLoaded(): boolean {
  const was = cartAlreadyLoaded;
  cartAlreadyLoaded = false;
  return was;
}

export function getOriginalDraftInvoiceId(): string | null {
  return readCacheIgnoringAge()?.originalDraftInvoiceId || null;
}

export function getOriginalHeldOrderId(): string | null {
  return readCacheIgnoringAge()?.originalHeldOrderId || null;
}

export function getOriginalQuotationId(): string | null {
  return readCacheIgnoringAge()?.originalQuotationId || null;
}

/** The held order's approval state / price breach as loaded into the cart, for the checkout gate. */
export function getOriginalHeldOrderApproval(): { state: string | null; priceBreach: number } {
  const cache = readCacheIgnoringAge();
  return {
    state: cache?.originalHeldOrderApprovalState ?? null,
    priceBreach: cache?.originalHeldOrderPriceBreach ?? 0,
  };
}

/**
 * Whether the held order in the cart is an M-Pesa order kept for its push, and that push: the
 * checkout picks it up instead of sending the customer another.
 */
export function getOriginalHeldOrderMpesa(): { isMpesaOrder: boolean; request: MpesaPushInfo | null } {
  const cache = readCacheIgnoringAge();
  if (!cache?.originalHeldOrderId) return { isMpesaOrder: false, request: null };
  return {
    isMpesaOrder: Boolean(cache.originalHeldOrderIsMpesa),
    request: cache.originalHeldOrderMpesaRequest ?? null,
  };
}

export function getOriginalOrderDiscountAmount(): number {
  return readCacheIgnoringAge()?.orderDiscountAmount || 0;
}

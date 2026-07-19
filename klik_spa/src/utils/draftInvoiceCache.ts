import { useCartStore } from '../stores/cartStore';
import type { CartItem, Customer } from '../../types';

interface DraftInvoiceCache {
  items: CartItem[];
  timestamp: number;
  invoiceId: string;
  customer: Customer | null;
  originalDraftInvoiceId: string; // draft SI (M-Pesa / legacy held)
  originalHeldOrderId?: string;   // SO-based held order
  orderDiscountAmount?: number;   // additional discount amount carried over from hold/draft
}

const CACHE_KEY = 'draft-invoice-cache';
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes

export function cacheDraftInvoiceItems(
  invoiceId: string,
  items: CartItem[],
  customer: Customer | null,
  orderDiscountAmount = 0,
): void {
  const cache: DraftInvoiceCache = {
    items,
    timestamp: Date.now(),
    invoiceId,
    customer,
    originalDraftInvoiceId: invoiceId,
    orderDiscountAmount,
  };

  localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
}

export function cacheHeldOrder(
  orderId: string,
  items: CartItem[],
  customer: Customer | null,
  orderDiscountAmount = 0,
): void {
  const cache: DraftInvoiceCache = {
    items,
    timestamp: Date.now(),
    invoiceId: orderId,
    customer,
    originalDraftInvoiceId: '',
    originalHeldOrderId: orderId,
    orderDiscountAmount,
  };
  localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
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

export function getOriginalDraftInvoiceId(): string | null {
  const cachedData = getCachedDraftInvoiceItems();
  return cachedData?.originalDraftInvoiceId || null;
}

export function getOriginalHeldOrderId(): string | null {
  const cachedData = getCachedDraftInvoiceItems();
  return cachedData?.originalHeldOrderId || null;
}

export function getOriginalOrderDiscountAmount(): number {
  const cachedData = getCachedDraftInvoiceItems();
  return cachedData?.orderDiscountAmount || 0;
}

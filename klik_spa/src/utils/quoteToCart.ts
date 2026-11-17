import { toast } from 'react-toastify';
import { getQuoteDetails } from '../services/quotation';
import { useCartStore } from '../stores/cartStore';
import { cacheQuote } from './draftInvoiceCache';
import { fillCartFromOrder } from './heldOrderToCart';

/** Load a saved quote into the cart as a sale. On a refusal it says why and leaves the cart. */
export async function addQuoteToCart(quoteId: string): Promise<boolean> {
  const details = await getQuoteDetails(quoteId);
  if (!details?.success) {
    toast.error(details?.message || 'Failed to load the quote');
    return false;
  }

  // Loading a quote is selling it: back to normal stock rules.
  useCartStore.getState().exitQuoteMode();
  const loaded = await fillCartFromOrder(details, (items, customer) =>
    cacheQuote(quoteId, items, customer, Number((details as { discount_amount?: number }).discount_amount) || 0),
  );

  if (loaded && details.expired) {
    await useCartStore.getState().refreshCartPricing();
    toast.info("Quote expired - re-priced at today's prices");
  }
  return loaded;
}

/**
 * A converted or closed quote's lines and customer, into the cart as a new sale at today's
 * prices. Not linked to the quote: it keeps the one invoice it already has.
 */
export async function addQuoteCopyToCart(quoteId: string): Promise<boolean> {
  const details = await getQuoteDetails(quoteId, true);
  if (!details?.success) {
    toast.error(details?.message || 'Failed to copy the quote');
    return false;
  }
  useCartStore.getState().exitQuoteMode();
  // A cache with no document behind it: the cart is a new sale.
  const loaded = await fillCartFromOrder(details, (items, customer) => cacheQuote('', items, customer, 0));
  if (loaded) {
    await useCartStore.getState().refreshCartPricing();
    toast.info(`Copied ${quoteId} as a new sale at today's prices`);
  }
  return loaded;
}

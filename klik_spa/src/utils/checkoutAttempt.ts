/**
 * The browser half of checkout idempotency.
 *
 * Every checkout carries a request id the server records in a Klik Checkout Request
 * ledger, so a retried submission returns the first invoice instead of creating a
 * second one. The id lives in localStorage rather than component state because the
 * cases worth protecting against — a mid-flight refresh, a crashed tab, a lost
 * response — are exactly the ones that destroy component state.
 *
 * The same cart plus the same payload reuses the same id. Change either and the
 * cashier is knowingly ringing up something else, so a new id is minted.
 */

const CHECKOUT_ATTEMPT_STORAGE_KEY = "klik-pos-checkout-attempt-v1";
const CHECKOUT_ATTEMPT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export interface CheckoutAttempt {
  requestId: string;
  cartFingerprint: string;
  requestFingerprint: string;
  createdAt: number;
  status: "pending" | "accepted";
  invoiceName?: string;
}

const stableValue = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((result, key) => {
        const child = (value as Record<string, unknown>)[key];
        if (child !== undefined) result[key] = stableValue(child);
        return result;
      }, {});
  }
  return value;
};

/** FNV-1a over a key-sorted JSON rendering, so object key order cannot change the hash. */
const fingerprint = (value: unknown): string => {
  const input = JSON.stringify(stableValue(value));
  let hash = 2166136261;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
};

const newRequestId = (): string => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `checkout-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const readAttempt = (): CheckoutAttempt | null => {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(CHECKOUT_ATTEMPT_STORAGE_KEY);
    if (!raw) return null;
    const attempt = JSON.parse(raw) as CheckoutAttempt;
    if (!attempt.requestId || Date.now() - Number(attempt.createdAt || 0) > CHECKOUT_ATTEMPT_MAX_AGE_MS) {
      window.localStorage.removeItem(CHECKOUT_ATTEMPT_STORAGE_KEY);
      return null;
    }
    return attempt;
  } catch {
    window.localStorage.removeItem(CHECKOUT_ATTEMPT_STORAGE_KEY);
    return null;
  }
};

const writeAttempt = (attempt: CheckoutAttempt) => {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(CHECKOUT_ATTEMPT_STORAGE_KEY, JSON.stringify(attempt));
  } catch {
    // A full or blocked store must never stop a sale; the checkout just loses its guard.
  }
};

export const getCheckoutCartFingerprint = (
  customerId: string | undefined,
  items: Array<Record<string, unknown>>,
): string => fingerprint({
  customerId: customerId || "",
  items: items.map((item) => ({
    itemCode: item.item_code || item.id,
    quantity: Number(item.quantity || 0),
    uom: item.uom || "",
    bundleEntries: item.bundle_entries || item.serial_batch_bundle || [],
  })),
});

export const getOrCreateCheckoutAttempt = (
  cartFingerprint: string,
  requestPayload: unknown,
): CheckoutAttempt => {
  const requestFingerprint = fingerprint(requestPayload);
  const existing = readAttempt();
  if (
    existing
    && existing.cartFingerprint === cartFingerprint
    && existing.requestFingerprint === requestFingerprint
  ) {
    return existing;
  }

  const attempt: CheckoutAttempt = {
    requestId: newRequestId(),
    cartFingerprint,
    requestFingerprint,
    createdAt: Date.now(),
    status: "pending",
  };
  writeAttempt(attempt);
  return attempt;
};

export const getCheckoutAttemptForCart = (cartFingerprint: string): CheckoutAttempt | null => {
  const attempt = readAttempt();
  return attempt?.cartFingerprint === cartFingerprint ? attempt : null;
};

export const markCheckoutAttemptAccepted = (requestId: string, invoiceName?: string) => {
  const attempt = readAttempt();
  if (!attempt || attempt.requestId !== requestId) return;
  writeAttempt({ ...attempt, status: "accepted", invoiceName });
};

export const clearCheckoutAttempt = (requestId?: string) => {
  if (typeof window === "undefined") return;
  const attempt = readAttempt();
  if (!requestId || !attempt || attempt.requestId === requestId) {
    try {
      window.localStorage.removeItem(CHECKOUT_ATTEMPT_STORAGE_KEY);
    } catch {
      // Nothing to do — the record expires on its own.
    }
  }
};

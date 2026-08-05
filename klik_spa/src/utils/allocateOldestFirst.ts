import type { PaymentAllocation, ReceivableInvoice } from "../services/paymentEntry";

export interface AllocationSplit {
  allocations: PaymentAllocation[];
  unallocated: number;
}

const round2 = (value: number) => Math.round(value * 100) / 100;

/**
 * Split a payment across a customer's invoices, filling each in turn.
 *
 * `invoices` is expected in the order the backend returns them — oldest due date
 * first — so this never re-derives that ordering, and it must not list the same
 * invoice twice, which the backend rejects outright. Whatever is left over is the
 * unallocated remainder, which the backend records as an advance.
 *
 * A non-finite `amount` allocates nothing. Callers pass `Number(inputValue)`, and
 * without this guard `Math.max(0, NaN)` is NaN, which every `<= 0` check below would
 * fall straight through — emitting a NaN allocation against every invoice.
 */
export function allocateOldestFirst(amount: number, invoices: ReceivableInvoice[]): AllocationSplit {
  let remaining = round2(Number.isFinite(amount) ? Math.max(0, amount) : 0);
  const allocations: PaymentAllocation[] = [];

  for (const invoice of invoices) {
    if (remaining <= 0) break;
    const allocated = round2(Math.min(remaining, invoice.outstanding));
    if (allocated <= 0) continue;
    allocations.push({ sales_invoice: invoice.name, allocated_amount: allocated });
    remaining = round2(remaining - allocated);
  }

  return { allocations, unallocated: remaining };
}

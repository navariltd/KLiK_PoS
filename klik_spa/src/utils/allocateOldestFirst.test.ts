import { describe, expect, it } from "vitest";
import { allocateOldestFirst, splitSingleInvoice } from "./allocateOldestFirst";
import type { ReceivableInvoice } from "../services/paymentEntry";

const invoice = (name: string, outstanding: number): ReceivableInvoice => ({
  name,
  posting_date: "2026-07-01",
  grand_total: outstanding,
  paid: 0,
  outstanding,
  due_date: "2026-07-01",
  days_overdue: 0,
  status: "Overdue",
});

describe("allocateOldestFirst", () => {
  it("fills invoices in the order given", () => {
    const result = allocateOldestFirst(1000, [invoice("INV-OLD", 600), invoice("INV-NEW", 400)]);
    expect(result.allocations).toEqual([
      { sales_invoice: "INV-OLD", allocated_amount: 600 },
      { sales_invoice: "INV-NEW", allocated_amount: 400 },
    ]);
    expect(result.unallocated).toBe(0);
  });

  it("partially fills the last invoice it reaches", () => {
    const result = allocateOldestFirst(800, [invoice("INV-OLD", 600), invoice("INV-NEW", 400)]);
    expect(result.allocations).toEqual([
      { sales_invoice: "INV-OLD", allocated_amount: 600 },
      { sales_invoice: "INV-NEW", allocated_amount: 200 },
    ]);
    expect(result.unallocated).toBe(0);
  });

  it("stops once the amount runs out", () => {
    const result = allocateOldestFirst(300, [invoice("INV-OLD", 600), invoice("INV-NEW", 400)]);
    expect(result.allocations).toEqual([{ sales_invoice: "INV-OLD", allocated_amount: 300 }]);
    expect(result.unallocated).toBe(0);
  });

  it("allocates nothing for a non-finite amount", () => {
    // Callers pass Number(inputValue); an unparseable field must not produce NaN rows.
    expect(allocateOldestFirst(Number("abc"), [invoice("INV-001", 500)])).toEqual({
      allocations: [],
      unallocated: 0,
    });
    expect(allocateOldestFirst(Infinity, [invoice("INV-001", 500)])).toEqual({
      allocations: [],
      unallocated: 0,
    });
  });

  it("leaves the remainder unallocated when the amount exceeds total outstanding", () => {
    const result = allocateOldestFirst(10100, [invoice("INV-00561", 2000)]);
    expect(result.allocations).toEqual([{ sales_invoice: "INV-00561", allocated_amount: 2000 }]);
    expect(result.unallocated).toBe(8100);
  });

  it("allocates nothing when there are no invoices", () => {
    const result = allocateOldestFirst(500, []);
    expect(result.allocations).toEqual([]);
    expect(result.unallocated).toBe(500);
  });

  it("allocates nothing for a zero or negative amount", () => {
    expect(allocateOldestFirst(0, [invoice("INV-001", 500)]).allocations).toEqual([]);
    expect(allocateOldestFirst(-50, [invoice("INV-001", 500)]).allocations).toEqual([]);
  });

  it("skips invoices with no outstanding balance", () => {
    const result = allocateOldestFirst(500, [invoice("INV-SETTLED", 0), invoice("INV-OPEN", 500)]);
    expect(result.allocations).toEqual([{ sales_invoice: "INV-OPEN", allocated_amount: 500 }]);
  });

  it("rounds to two decimals rather than accumulating float drift", () => {
    const result = allocateOldestFirst(0.3, [invoice("INV-001", 0.1), invoice("INV-002", 0.2)]);
    expect(result.allocations).toEqual([
      { sales_invoice: "INV-001", allocated_amount: 0.1 },
      { sales_invoice: "INV-002", allocated_amount: 0.2 },
    ]);
    expect(result.unallocated).toBe(0);
  });
});

describe("splitSingleInvoice", () => {
  it("settles the invoice and leaves the excess unallocated", () => {
    expect(splitSingleInvoice(2500, 2000)).toEqual({ allocated: 2000, unallocated: 500 });
  });

  it("allocates everything when the amount is below the outstanding", () => {
    expect(splitSingleInvoice(500, 2000)).toEqual({ allocated: 500, unallocated: 0 });
  });

  it("allocates exactly on an exact payment", () => {
    expect(splitSingleInvoice(2000, 2000)).toEqual({ allocated: 2000, unallocated: 0 });
  });

  it("allocates nothing for a non-finite amount", () => {
    expect(splitSingleInvoice(Number("abc"), 2000)).toEqual({ allocated: 0, unallocated: 0 });
  });

  it("allocates nothing when the invoice has no outstanding", () => {
    expect(splitSingleInvoice(500, 0)).toEqual({ allocated: 0, unallocated: 500 });
  });

  it("rounds to two decimals", () => {
    expect(splitSingleInvoice(0.3, 0.1)).toEqual({ allocated: 0.1, unallocated: 0.2 });
  });
});

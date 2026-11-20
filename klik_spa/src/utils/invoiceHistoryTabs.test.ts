import { describe, expect, it } from "vitest";
import { countFailed, matchesInvoiceTab, normalizeInvoiceTab, rowsForTab, sortFailedFirst, tabPagesInvoices } from "./invoiceHistoryTabs";

const row = (status: string, queueStatus?: string) => ({ status, queueStatus });

describe("Invoice History tabs", () => {
  it("Queued holds everything the server has not finished: queued, processing and failed", () => {
    expect(matchesInvoiceTab(row("Draft", "Queued"), "queued")).toBe(true);
    expect(matchesInvoiceTab(row("Draft", "Processing"), "queued")).toBe(true);
    expect(matchesInvoiceTab(row("Draft", " failed "), "queued")).toBe(true);
    expect(matchesInvoiceTab(row("Paid", ""), "queued")).toBe(false);
    expect(matchesInvoiceTab(row("Paid", "Completed"), "queued")).toBe(false);
  });

  it("other tabs match on the invoice status; all matches everything", () => {
    expect(matchesInvoiceTab(row(" Paid "), "Paid")).toBe(true);
    expect(matchesInvoiceTab(row("Unpaid"), "Paid")).toBe(false);
    expect(matchesInvoiceTab(row("Return"), "all")).toBe(true);
  });

  it("Held holds every held order, whatever its approval status reads", () => {
    expect(matchesInvoiceTab({ status: "Approval pending", isHeldOrder: true }, "Held")).toBe(true);
    expect(matchesInvoiceTab({ status: "Held", isHeldOrder: true }, "Held")).toBe(true);
    expect(matchesInvoiceTab(row("Paid"), "Held")).toBe(false);
  });

  it("the old Failed Queue link opens the merged Queued tab", () => {
    expect(normalizeInvoiceTab("queue_failed")).toBe("queued");
    expect(normalizeInvoiceTab("Paid")).toBe("Paid");
  });

  it("failed rows sort to the top, keeping the order of the rest", () => {
    const rows = [row("Draft", "Queued"), row("Draft", "Failed"), row("Draft", "Processing"), row("Draft", "failed")];
    expect(sortFailedFirst(rows).map((r) => r.queueStatus)).toEqual(["Failed", "failed", "Queued", "Processing"]);
    expect(countFailed(rows)).toBe(2);
  });

  it("Quotes holds quote rows only, and only quote rows reach it", () => {
    const quote = { status: "Expired", isQuote: true };
    expect(matchesInvoiceTab(quote, "Quotes")).toBe(true);
    expect(matchesInvoiceTab(row("Paid"), "Quotes")).toBe(false);
    const sources: Record<"invoices" | "heldOrders" | "quotes", { status: string; isQuote?: boolean }[]> =
      { invoices: [row("Paid")], heldOrders: [row("Held")], quotes: [quote] };
    expect(rowsForTab("Quotes", sources)).toEqual([quote]);
    expect(rowsForTab("Held", sources)).toEqual(sources.heldOrders);
    expect(rowsForTab("all", sources)).toEqual(sources.invoices);
  });

  it("Held and Quotes never offer to load more invoices: their rows are not invoice pages", () => {
    expect(tabPagesInvoices("Held")).toBe(false);
    expect(tabPagesInvoices("Quotes")).toBe(false);
    expect(tabPagesInvoices("all")).toBe(true);
    expect(tabPagesInvoices("Paid")).toBe(true);
  });
});

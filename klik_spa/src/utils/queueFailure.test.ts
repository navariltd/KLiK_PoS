import { describe, expect, it } from "vitest";
import { formatQueueFailure } from "./queueFailure";

describe("formatQueueFailure", () => {
  it("names the invoice, the customer and the reason", () => {
    expect(
      formatQueueFailure({
        invoice_name: "ACC-SINV-2026-00020",
        customer: "Derick",
        error: "Insufficient Permission for Stock Reservation Entry",
      }),
    ).toBe(
      "Invoice ACC-SINV-2026-00020 for Derick was not submitted: Insufficient Permission for Stock Reservation Entry",
    );
  });

  it("drops the customer when the event omits one", () => {
    expect(formatQueueFailure({ invoice_name: "INV-1", error: "boom" })).toBe(
      "Invoice INV-1 was not submitted: boom",
    );
  });

  it("says so plainly when no reason was recorded", () => {
    // Silence is the failure mode being fixed - never render an empty alert.
    expect(formatQueueFailure({ invoice_name: "INV-1" })).toBe(
      "Invoice INV-1 was not submitted. No reason was recorded.",
    );
  });

  it("still reports something useful for a malformed event", () => {
    expect(formatQueueFailure(null)).toBe("An invoice was not submitted. No reason was recorded.");
    expect(formatQueueFailure({})).toBe("An invoice was not submitted. No reason was recorded.");
  });

  it("ignores whitespace-only fields", () => {
    expect(formatQueueFailure({ invoice_name: "INV-1", customer: "   ", error: "  " })).toBe(
      "Invoice INV-1 was not submitted. No reason was recorded.",
    );
  });
});

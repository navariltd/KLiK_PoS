import { describe, expect, it } from "vitest";
import {
  describeUnresolvedFailure,
  formatQueueFailure,
  stripHtml,
  summariseUnresolvedFailures,
} from "./queueFailure";

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

describe("summariseUnresolvedFailures", () => {
  it("says nothing when the till is clean", () => {
    expect(summariseUnresolvedFailures([])).toBe("");
  });

  it("uses the singular for one unposted sale", () => {
    expect(summariseUnresolvedFailures([{ invoice_name: "INV-1" }])).toBe(
      "1 sale did not post and is not recorded yet.",
    );
  });

  it("uses the plural for several", () => {
    expect(
      summariseUnresolvedFailures([{ invoice_name: "INV-1" }, { invoice_name: "INV-2" }]),
    ).toBe("2 sales did not post and are not recorded yet.");
  });
});

describe("describeUnresolvedFailure", () => {
  it("names the invoice, customer, amount and reason", () => {
    expect(
      describeUnresolvedFailure({
        invoice_name: "ACC-SINV-2026-00042",
        customer: "ZAODON",
        grand_total: 1430,
        currency: "KES",
        error: "Insufficient Permission",
      }),
    ).toBe("ACC-SINV-2026-00042 · ZAODON · KES 1430 — Insufficient Permission");
  });

  it("degrades to just the invoice when nothing else is known", () => {
    expect(describeUnresolvedFailure({ invoice_name: "INV-1" })).toBe("INV-1");
  });

  it("omits a zero total rather than showing a misleading amount", () => {
    expect(describeUnresolvedFailure({ invoice_name: "INV-1", grand_total: 0 })).toBe("INV-1");
  });
});

describe("stripHtml", () => {
  it("removes markup that frappe.throw embeds in messages", () => {
    expect(
      stripHtml("Insufficient stock for item <strong>BOSCH DISC</strong> in <strong>Main</strong>."),
    ).toBe("Insufficient stock for item BOSCH DISC in Main.");
  });

  it("decodes the entities that come with it", () => {
    expect(stripHtml("A&amp;B &lt;tag&gt;&nbsp;here")).toBe("A&B <tag> here");
  });

  it("is empty for nothing", () => {
    expect(stripHtml(undefined)).toBe("");
    expect(stripHtml(null)).toBe("");
  });
});

describe("markup in rendered failure text", () => {
  it("never reaches the toast", () => {
    expect(
      formatQueueFailure({ invoice_name: "INV-1", error: "no <strong>stock</strong>" }),
    ).toBe("Invoice INV-1 was not submitted: no stock");
  });

  it("never reaches the banner row", () => {
    expect(
      describeUnresolvedFailure({ invoice_name: "INV-1", error: "no <strong>stock</strong>" }),
    ).toBe("INV-1 — no stock");
  });
});

describe("summariseUnresolvedFailures with a capped list", () => {
  it("counts every unposted sale, not just the listed ones", () => {
    // The endpoint caps the list at 20; reporting 20 to a till that has 40 would be the
    // silent truncation this banner exists to prevent.
    expect(summariseUnresolvedFailures([{ invoice_name: "INV-1" }], 40)).toBe(
      "40 sales did not post and are not recorded yet.",
    );
  });

  it("never reports fewer than it is showing", () => {
    expect(
      summariseUnresolvedFailures([{ invoice_name: "INV-1" }, { invoice_name: "INV-2" }], 0),
    ).toBe("2 sales did not post and are not recorded yet.");
  });
});

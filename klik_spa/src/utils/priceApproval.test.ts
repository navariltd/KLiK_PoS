import { describe, expect, it } from "vitest";
import {
  approvalBadge,
  heldStatusLabel,
  priceApprovalMessage,
  PRICE_APPROVAL_PENDING,
  PRICE_APPROVED,
} from "./priceApproval";

describe("approvalBadge", () => {
  it("shows an amber pending badge while approval is pending", () => {
    expect(approvalBadge(PRICE_APPROVAL_PENDING, 1)).toEqual({ label: "Approval pending", tone: "amber" });
  });

  it("shows an amber pending badge regardless of the breach flag", () => {
    expect(approvalBadge(PRICE_APPROVAL_PENDING, 0)).toEqual({ label: "Approval pending", tone: "amber" });
  });

  it("shows a green approved badge once approved", () => {
    expect(approvalBadge(PRICE_APPROVED, 1)).toEqual({ label: "Price approved", tone: "green" });
  });

  it("shows a green approved badge regardless of the breach flag", () => {
    expect(approvalBadge(PRICE_APPROVED, 0)).toEqual({ label: "Price approved", tone: "green" });
  });

  it("shows a red needs-approval badge for a breaching draft with no request yet", () => {
    expect(approvalBadge("Draft", 1)).toEqual({ label: "Needs approval", tone: "red" });
  });

  it("shows a red needs-approval badge for a null state with a breach", () => {
    expect(approvalBadge(null, 1)).toEqual({ label: "Needs approval", tone: "red" });
    expect(approvalBadge(undefined, true)).toEqual({ label: "Needs approval", tone: "red" });
  });

  it("is null for a clean draft order", () => {
    expect(approvalBadge("Draft", 0)).toBeNull();
    expect(approvalBadge(null, null)).toBeNull();
    expect(approvalBadge(undefined, undefined)).toBeNull();
  });
});

describe("priceApprovalMessage", () => {
  it("is null when there is no breach", () => {
    expect(priceApprovalMessage(null, 0)).toBeNull();
    expect(priceApprovalMessage("Draft", 0)).toBeNull();
    expect(priceApprovalMessage(PRICE_APPROVAL_PENDING, 0)).toBeNull();
  });

  it("is null once approved, even if the breach flag is still set", () => {
    expect(priceApprovalMessage(PRICE_APPROVED, 1)).toBeNull();
  });

  it("asks to wait while approval is pending", () => {
    expect(priceApprovalMessage(PRICE_APPROVAL_PENDING, 1)).toBe(
      "Prices below the minimum need approval. Wait for approval on the held order.",
    );
  });

  it("asks to hold the order to request approval otherwise", () => {
    expect(priceApprovalMessage("Draft", 1)).toBe(
      "Prices below the minimum need approval. Hold the order to request it.",
    );
    expect(priceApprovalMessage(null, 1)).toBe(
      "Prices below the minimum need approval. Hold the order to request it.",
    );
  });
});

describe("heldStatusLabel", () => {
  it("is the approval state when there is one, else Held", () => {
    expect(heldStatusLabel(null, 0)).toBe("Held");
    expect(heldStatusLabel(null, 1)).toBe("Needs approval");
    expect(heldStatusLabel(PRICE_APPROVAL_PENDING, 1)).toBe("Approval pending");
    expect(heldStatusLabel(PRICE_APPROVED, 1)).toBe("Price approved");
  });
});

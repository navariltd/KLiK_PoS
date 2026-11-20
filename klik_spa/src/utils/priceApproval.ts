export const PRICE_APPROVAL_PENDING = "Price Approval Pending";
export const PRICE_APPROVED = "Price Approved";
export const WITHDRAW_APPROVAL = "Withdraw Approval";

export type ApprovalTone = "amber" | "green" | "red";

export function approvalBadge(
  state: string | null | undefined,
  priceBreach: number | boolean | null | undefined,
): { label: string; tone: ApprovalTone } | null {
  if (state === PRICE_APPROVAL_PENDING) return { label: "Approval pending", tone: "amber" };
  if (state === PRICE_APPROVED) return { label: "Price approved", tone: "green" };
  if (priceBreach) return { label: "Needs approval", tone: "red" };
  return null;
}

/** The Held tab's Status: the approval state when there is one, else plain "Held". */
export const heldStatusLabel = (
  state: string | null | undefined,
  priceBreach: number | boolean | null | undefined,
): string => approvalBadge(state, priceBreach)?.label ?? "Held";

/** Why a held order cannot be checked out yet, or null when it can. */
export function priceApprovalMessage(
  state: string | null | undefined,
  priceBreach: number | boolean | null | undefined,
): string | null {
  if (!priceBreach || state === PRICE_APPROVED) return null;
  if (state === PRICE_APPROVAL_PENDING) {
    return "Prices below the minimum need approval. Wait for approval on the held order.";
  }
  return "Prices below the minimum need approval. Hold the order to request it.";
}

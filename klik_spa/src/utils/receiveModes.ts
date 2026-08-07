import type { PaymentMode } from "../hooks/usePaymentModes";

/**
 * Payment modes offered when receiving a customer payment.
 *
 * Phone modes are excluded: they are M-Pesa, and there is no M-Pesa receive
 * integration yet, so recording one here would book money that was never validated
 * against a real M-Pesa transaction. The sale flow (PaymentDialog) and the opening
 * entry dialog still offer them — that is why this filter lives here and not in
 * get_payment_modes or the usePaymentModes hook, which those components share.
 */
export function selectableReceiveModes(modes: PaymentMode[]): PaymentMode[] {
  return (modes || []).filter((mode) => mode.type !== "Phone");
}

/**
 * The mode to preselect. Computed from the FILTERED list — picking the profile
 * default first would leave the select empty whenever that default is a Phone mode.
 */
export function defaultReceiveMode(modes: PaymentMode[]): string {
  const selectable = selectableReceiveModes(modes);
  return (
    selectable.find((mode) => mode.default === 1)?.mode_of_payment ||
    selectable[0]?.mode_of_payment ||
    ""
  );
}

/**
 * ERPNext's validate_transaction_reference throws "Reference No and Reference Date is
 * mandatory for Bank transaction" when the receiving account is of type Bank. Keying off
 * the account type rather than the mode type matters: an M-Pesa mode is type Phone but
 * lands in a Bank account.
 */
export function requiresReference(modes: PaymentMode[], modeOfPayment: string): boolean {
  if (!modeOfPayment) return false;
  const selected = (modes || []).find((mode) => mode.mode_of_payment === modeOfPayment);
  return selected?.account_type === "Bank";
}

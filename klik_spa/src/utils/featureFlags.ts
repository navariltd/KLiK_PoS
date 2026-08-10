/**
 * Build-time feature switches.
 *
 * These are deliberately plain constants rather than settings: they turn off features that are
 * not merely unconfigured but cannot legally or operationally be used, so an operator should
 * not be able to switch them back on from the UI by mistake.
 */

/**
 * Multi-Invoice Return — returning items across several invoices in one credit note.
 *
 * OFF because Kenya's TIMS/eTIMS rules require a credit note to reference the single original
 * invoice it reverses, so one note spanning several invoices cannot be transmitted correctly.
 * Single-invoice returns are unaffected and remain available.
 *
 * The feature's code is intentionally left in place rather than deleted — flip this to true to
 * restore it for a deployment where the requirement does not apply.
 */
export const MULTI_INVOICE_RETURN_ENABLED = false;

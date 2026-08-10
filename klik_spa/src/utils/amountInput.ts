/**
 * Helpers for a thousands-separated amount field.
 *
 * The component keeps its state RAW — digits and at most one dot, never a separator — and only
 * the *displayed* value carries commas. That split matters: the amount is parsed with Number()
 * and fed to the allocation split, and Number("349,903") is NaN. A NaN amount has already caused
 * a bug here once, where every `<= 0` guard fell through because NaN fails all comparisons.
 */

/** Strip a typed value down to something Number() can always parse (or ""). */
export function stripAmountInput(raw: string): string {
  if (!raw) return "";

  // Drop everything that is not a digit or a dot — commas the user pasted, currency symbols,
  // spaces, minus signs. A payment received is never negative.
  const cleaned = raw.replace(/[^\d.]/g, "");
  if (!cleaned) return "";

  // Keep only the first dot; "1.2.3" becomes "1.23" rather than NaN.
  const parts = cleaned.split(".");
  const whole = parts[0] ?? "";
  const rest = parts.slice(1);
  if (rest.length === 0) return whole;

  // Money is 2dp. Truncate rather than round: rounding up here would let someone allocate a
  // cent more than they typed.
  const decimals = rest.join("").slice(0, 2);
  return `${whole}.${decimals}`;
}

/** Group the integer part for display. Input must already be stripped. */
export function formatAmountInput(stripped: string): string {
  if (!stripped) return "";

  const [whole, decimals] = stripped.split(".");
  const grouped = whole ? Number(whole).toLocaleString("en-US") : "";

  // A trailing dot is preserved so the field does not fight the user mid-keystroke: typing
  // "1500." must not have its dot eaten before the decimals arrive.
  if (decimals === undefined) return grouped;
  return `${grouped}.${decimals}`;
}

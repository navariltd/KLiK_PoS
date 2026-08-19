/**
 * Wording for the POS permission preflight.
 *
 * klik_pos degrades rather than erroring when a doctype is unreadable, so a permission gap
 * otherwise arrives as a plausible wrong number - zero stock, a missing price - with nothing
 * saying why. This turns the backend's report into a sentence a cashier can act on and, more
 * importantly, hand to whoever administers their roles.
 */

export interface PermissionGap {
  doctype: string;
  permission: string;
  severity: "critical" | "degraded";
  consequence: string;
  granting_roles?: string[];
}

export interface PermissionHealth {
  healthy: boolean;
  missing: PermissionGap[];
  has_critical: boolean;
}

/** "Bin", "Bin and Item Price", "Bin, Item Price and Warehouse" */
export function listDoctypes(gaps: PermissionGap[]): string {
  const names = gaps.map((gap) => gap.doctype);
  if (names.length === 0) return "";
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** Roles that would fix every listed gap, de-duplicated across them. */
export function listRemedyRoles(gaps: PermissionGap[]): string[] {
  const roles = new Set<string>();
  for (const gap of gaps) {
    for (const role of gap.granting_roles ?? []) {
      roles.add(role);
    }
  }
  return [...roles].sort();
}

/**
 * One sentence naming what is missing, what it costs, and who can fix it.
 * Returns "" when there is nothing to say, so the caller renders nothing.
 */
export function summarisePermissionHealth(health: PermissionHealth | null | undefined): string {
  const gaps = health?.missing ?? [];
  if (!gaps.length) return "";

  const lead = health?.has_critical
    ? `Your role is missing access the POS needs: ${listDoctypes(gaps)}.`
    : `Your role cannot read ${listDoctypes(gaps)}, so some figures are incomplete.`;

  const consequences = gaps.map((gap) => gap.consequence).filter(Boolean).join(" ");
  const roles = listRemedyRoles(gaps);
  const remedy = roles.length
    ? ` An administrator can restore this with the ${roles.join(" or ")} role${roles.length > 1 ? "s" : ""}.`
    : " Ask an administrator to grant the missing access.";

  return `${lead} ${consequences}${remedy}`.replace(/\s+/g, " ").trim();
}

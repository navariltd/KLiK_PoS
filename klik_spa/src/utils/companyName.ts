import type { POSCompanyDetails } from "../stores/posProfileStore";

/**
 * The company docname from a POS Profile, whose `company` is either the docname itself or an
 * expanded object. Prefer `name`: that is the docname the API filters on, whereas
 * `company_name` is the display title and will not match.
 */
export function resolveCompanyName(company: string | POSCompanyDetails | undefined | null): string {
  if (!company) return "";
  if (typeof company === "string") return company.trim();
  return (company.name || company.company_name || "").trim();
}

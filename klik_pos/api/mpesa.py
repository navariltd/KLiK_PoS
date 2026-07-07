"""Read-only search endpoint backing the POS checkout's "M-Pesa Payment
Options" reconciliation modal.

Queries the `Mpesa C2B Payment Register` doctype (owned by the
`frappe_mpsa_payments` app) directly via `frappe.get_all`/`frappe.db.count` —
normal, unprivileged, same-site cross-app data access that requires no code
change in the owning app. This mirrors the query pattern used by
`cecypo_powerpack.quick_pay.api.list_pending_mpesa_payments`.
"""

import frappe


def _mpesa_shortcodes_for_company(company: str) -> list[str]:
	"""Return all distinct, non-empty M-Pesa business shortcodes configured
	for `company` (a company can have more than one, e.g. paybill + till)."""
	rows = frappe.get_all(
		"Mpesa Settings",
		filters={"company": company},
		fields=["business_shortcode"],
	)
	shortcodes = {str(r["business_shortcode"]) for r in rows if r.get("business_shortcode")}
	return sorted(shortcodes)


@frappe.whitelist()
def get_mpesa_payments(
	company: str,
	pos_profile: str | None = None,
	mode_of_payment: str | None = None,
	search: str | None = None,
) -> dict:
	"""Return pending Mpesa C2B Payment Register rows for `company`, optionally
	filtered by a 3+ character `search` term.

	`mode_of_payment` is accepted for query-string compatibility with the
	frontend but is intentionally NOT used to filter: a pending row's
	`mode_of_payment` is only populated later, via a separate
	`Mpesa C2B Payment Register URL` lookup in that doctype's
	`set_missing_values()`, so it's NULL on most pending rows pre-reconciliation.
	Filtering on it would exclude every unassigned row.
	"""
	shortcodes = _mpesa_shortcodes_for_company(company)
	if not shortcodes:
		return {"count": 0, "payments": [], "shortcodes": []}

	base_filters = {"docstatus": 0, "businessshortcode": ["in", shortcodes]}
	total_count = frappe.db.count("Mpesa C2B Payment Register", base_filters)

	payments = []
	search = (search or "").strip()
	if len(search) >= 3:
		s = f"%{search}%"
		payments = frappe.get_all(
			"Mpesa C2B Payment Register",
			filters=base_filters,
			or_filters=[
				["full_name", "like", s],
				["transid", "like", s],
				["billrefnumber", "like", s],
				["msisdn", "like", s],
			],
			fields=[
				"name",
				"full_name",
				"transamount",
				"transid",
				"msisdn",
				"posting_date",
				"billrefnumber",
				"businessshortcode",
				"creation",
			],
			order_by="creation desc",
			limit_page_length=100,
		)

	return {"count": total_count, "payments": payments, "shortcodes": shortcodes}

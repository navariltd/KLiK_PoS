"""Read-only search endpoint backing the POS checkout's "M-Pesa Payment
Options" reconciliation modal.

Queries the `Mpesa C2B Payment Register` doctype (owned by the
`frappe_mpsa_payments` app) directly via `frappe.get_all`/`frappe.db.count` —
normal, unprivileged, same-site cross-app data access that requires no code
change in the owning app. This mirrors the query pattern used by
`cecypo_powerpack.quick_pay.api.list_pending_mpesa_payments`.
"""

import frappe
from frappe import _
from frappe.utils import flt


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


@frappe.whitelist()
def process_mpesa(
	doctype: str,
	invoice_name: str,
	customer: str,
	mpesa_payments: str,
	mode_of_payment: str,
	auto_save: int = 1,
	auto_submit: int = 0,
	merge_payments: int = 0,
) -> dict:
	"""Reconcile one or more pending `Mpesa C2B Payment Register` rows onto a
	draft (unsubmitted) `Sales Invoice`, backing the POS checkout's "Add
	Selected Payments" button in the "M-Pesa Payment Options" modal.

	Unlike cecypo_powerpack's `process_mpesa_quick_pay` (which reconciles
	against a *submitted* Sales Order via standalone Payment Entry docs),
	klik_pos reconciles against a *draft* Sales Invoice that already has its
	own `payments` child table, so rows are appended directly to
	`invoice.payments` instead.

	Ordering / safety:
	  1. Load + validate the invoice and every named register row up front.
	     Nothing is mutated during validation.
	  2. Apply the invoice-side effect (append payment rows + traceability
	     rows, save, optionally submit) FIRST.
	  3. Only after that succeeds, consume (submit) each register row. If a
	     register row fails to submit at this point (e.g. concurrently
	     consumed by another request — Frappe's optimistic-concurrency check
	     raises TimestampMismatchError on a stale `modified` timestamp), the
	     error surfaces but the already-successful invoice update is not
	     rolled back. This matches the "consume immediately" design decision.

	`doctype` is currently only ever "Sales Invoice" — this app never
	targets Sales Order for this flow.
	"""
	if doctype != "Sales Invoice":
		frappe.throw(_("Mpesa reconciliation only supports Sales Invoice, got {0}").format(doctype))

	auto_submit = int(auto_submit or 0)
	merge_payments = int(merge_payments or 0)

	invoice = frappe.get_doc("Sales Invoice", invoice_name)
	if invoice.docstatus != 0:
		frappe.throw(
			_("Sales Invoice {0} is not a draft (docstatus={1}); Mpesa payments can only be reconciled onto a draft invoice.").format(
				invoice_name, invoice.docstatus
			)
		)
	if invoice.customer != customer:
		frappe.throw(
			_("Sales Invoice {0} belongs to customer {1}, not {2}.").format(
				invoice_name, invoice.customer, customer
			)
		)

	names = [n.strip() for n in (mpesa_payments or "").split(",") if n.strip()]
	if not names:
		frappe.throw(_("No Mpesa register payments were selected."))

	duplicates = sorted({n for n in names if names.count(n) > 1})
	if duplicates:
		frappe.throw(
			_("The same Mpesa register payment was selected more than once: {0}").format(
				", ".join(duplicates)
			)
		)

	register_rows = []
	invalid = []
	for name in names:
		if not frappe.db.exists("Mpesa C2B Payment Register", name):
			invalid.append(_("{0} (not found)").format(name))
			continue
		row = frappe.get_doc("Mpesa C2B Payment Register", name)
		if row.docstatus != 0:
			invalid.append(_("{0} (already consumed, docstatus={1})").format(name, row.docstatus))
			continue
		if not flt(row.transamount) > 0:
			invalid.append(_("{0} (invalid amount: {1})").format(name, row.transamount))
			continue
		register_rows.append(row)

	if invalid:
		frappe.throw(_("Cannot reconcile the following Mpesa payment(s): {0}").format("; ".join(invalid)))

	total_amount = sum(flt(row.transamount) for row in register_rows)

	if merge_payments:
		reference_no = ",".join(row.transid for row in register_rows if row.transid)
		phone_number = next((row.msisdn for row in register_rows if row.msisdn), None)
		invoice.append(
			"payments",
			{
				"mode_of_payment": mode_of_payment,
				"amount": total_amount,
				"reference_no": reference_no,
				"phone_number": phone_number,
			},
		)
		payments_added = [
			{"mode_of_payment": mode_of_payment, "amount": total_amount, "reference": reference_no}
		]
	else:
		payments_added = []
		for row in register_rows:
			invoice.append(
				"payments",
				{
					"mode_of_payment": mode_of_payment,
					"amount": row.transamount,
					"reference_no": row.transid,
					"phone_number": row.msisdn,
				},
			)
			payments_added.append(
				{"mode_of_payment": mode_of_payment, "amount": row.transamount, "reference": row.transid}
			)

	# Traceability: one child row per consumed register row, even when
	# merged into a single invoice payment line.
	for row in register_rows:
		invoice.append(
			"custom_mpesa_reconciled_payments",
			{
				"mpesa_c2b_payment_register": row.name,
				"transid": row.transid,
				"amount": row.transamount,
				"msisdn": row.msisdn,
			},
		)

	invoice.save()
	if auto_submit:
		invoice.submit()

	# Only now consume the register rows -- the invoice side effect above
	# already succeeded, so a failure here (e.g. concurrent consumption)
	# doesn't roll back the invoice.
	for row in register_rows:
		row.customer = customer
		row.mode_of_payment = mode_of_payment
		if not row.company:
			row.company = invoice.company
		row.save(ignore_permissions=True)
		row.submit()

	return {
		"success": True,
		"payments_added": payments_added,
		"mpesa_payments": [{"name": row.name, "amount": row.transamount} for row in register_rows],
		"total_amount": total_amount,
		"merged": bool(merge_payments),
		"saved": True,
		"submitted": bool(auto_submit),
	}

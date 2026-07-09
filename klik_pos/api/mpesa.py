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
	"""Record one or more pending `Mpesa C2B Payment Register` rows against a
	draft (unsubmitted) `Sales Invoice`, backing the POS checkout's "Add
	Selected Payments" button in the "M-Pesa Payment Options" modal.

	This does NOT append rows to the invoice's own `payments` child table and
	does NOT consume (submit) the register rows. It only records
	traceability rows on `invoice.custom_mpesa_reconciled_payments` recording
	which register rows the cashier selected and which Mode of Payment to use
	for each. The actual Payment Entry creation + reconciliation against the
	invoice's outstanding amount happens later, once the invoice is actually
	submitted — see `_finalize_mpesa_reconciliation`, called either inline
	here (when `auto_submit=1`) or from `submit_draft_invoice` (the normal
	POS checkout path, which submits separately after this call returns).

	Deferring consumption this way means a held/draft order with Mpesa
	payments selected can still be freely edited or abandoned without ever
	having consumed a real M-Pesa receipt.

	`doctype` is currently only ever "Sales Invoice" — this app never
	targets Sales Order for this flow.
	"""
	if doctype != "Sales Invoice":
		frappe.throw(_("Mpesa reconciliation only supports Sales Invoice, got {0}").format(doctype))

	if not int(auto_save or 0):
		frappe.throw(
			_(
				"auto_save=0 is not supported: process_mpesa always saves the invoice. "
				"Only auto_save=1 is implemented."
			)
		)

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
		payments_added = [
			{"mode_of_payment": mode_of_payment, "amount": total_amount, "reference": reference_no}
		]
	else:
		payments_added = [
			{"mode_of_payment": mode_of_payment, "amount": row.transamount, "reference": row.transid}
			for row in register_rows
		]

	# Traceability only -- the register rows themselves stay untouched
	# (docstatus=0) until the invoice is actually submitted; see
	# `_finalize_mpesa_reconciliation`.
	for row in register_rows:
		invoice.append(
			"custom_mpesa_reconciled_payments",
			{
				"mpesa_c2b_payment_register": row.name,
				"transid": row.transid,
				"amount": row.transamount,
				"msisdn": row.msisdn,
				"mode_of_payment": mode_of_payment,
			},
		)

	invoice.save()

	result = {
		"success": True,
		"payments_added": payments_added,
		"mpesa_payments": [{"name": row.name, "amount": row.transamount} for row in register_rows],
		"total_amount": total_amount,
		"merged": bool(merge_payments),
		"saved": True,
		"submitted": False,
	}

	if auto_submit:
		embed_summary = _embed_mpesa_payments(invoice)
		invoice.submit()
		result["submitted"] = True
		result["mpesa_reconciliation"] = _finalize_mpesa_reconciliation(invoice, embed_summary)

	return result


def _pending_mpesa_rows(invoice) -> list:
	"""Recorded `custom_mpesa_reconciled_payments` rows whose underlying
	`Mpesa C2B Payment Register` row is still unconsumed (docstatus=0)."""
	return [
		child
		for child in invoice.get("custom_mpesa_reconciled_payments") or []
		if frappe.db.get_value("Mpesa C2B Payment Register", child.mpesa_c2b_payment_register, "docstatus") == 0
	]


def _embed_mpesa_payments(invoice) -> dict:
	"""Pre-submit phase of the hybrid M-Pesa flow.

	Embeds the recorded M-Pesa receipts into the *draft* invoice's own
	`payments` child table, capped so the embedded total never exceeds the
	invoice's payable total. This satisfies ERPNext's POS paid-amount
	validation, balances the invoice GL through the normal POS path, and makes
	the M-Pesa take visible to POS shift/drawer reconciliation -- which JOINs
	`Sales Invoice Payment` and never sees Payment Entries.

	The overpaid remainder (received - embedded) is reported in the returned
	summary so `_finalize_mpesa_reconciliation` can turn it into an unallocated
	Payment Entry credit after submit. M-Pesa never hands back physical change,
	so the embedded portion is capped and `change_amount` stays 0.

	Must be called on a draft invoice (docstatus=0). Returns a summary consumed
	by the post-submit finalizer:
	  {received_total, embedded_total, excess_by_mode, excess_registers_by_mode}
	No-op (all-zero summary) when there are no pending recorded M-Pesa rows.
	"""
	empty = {"received_total": 0.0, "embedded_total": 0.0, "excess_by_mode": {}, "excess_registers_by_mode": {}}

	if invoice.docstatus != 0:
		frappe.throw(
			_("Cannot embed Mpesa payments on {0}: invoice is not a draft (docstatus={1}).").format(
				invoice.name, invoice.docstatus
			)
		)

	pending = _pending_mpesa_rows(invoice)
	if not pending:
		return empty

	received_total = sum(flt(c.amount) for c in pending)

	payable = flt(invoice.rounded_total) or flt(invoice.grand_total)
	existing_payments = sum(flt(p.amount) for p in invoice.get("payments") or [])
	capacity = max(payable - existing_payments, 0.0)

	# Fill capacity in selection (FIFO) order across the recorded rows. A row
	# may straddle the cap: part embedded, remainder excess. Track per mode so
	# each embedded payment row and each excess Payment Entry uses the correct
	# Mode-of-Payment account.
	remaining = capacity
	embedded_by_mode: dict = {}
	refs_by_mode: dict = {}
	excess_by_mode: dict = {}
	excess_registers_by_mode: dict = {}

	for c in pending:
		amt = flt(c.amount)
		mode = c.mode_of_payment
		take = min(amt, remaining) if remaining > 0 else 0.0
		if take > 0:
			embedded_by_mode[mode] = embedded_by_mode.get(mode, 0.0) + take
			if c.transid:
				refs_by_mode.setdefault(mode, []).append(c.transid)
			remaining -= take
		leftover = amt - take
		if leftover > 0:
			excess_by_mode[mode] = excess_by_mode.get(mode, 0.0) + leftover
			excess_registers_by_mode.setdefault(mode, []).append(c.mpesa_c2b_payment_register)

	for mode, amt in embedded_by_mode.items():
		if amt <= 0:
			continue
		invoice.append(
			"payments",
			{
				"mode_of_payment": mode,
				"amount": amt,
				"reference_no": ",".join(refs_by_mode.get(mode, [])) or None,
			},
		)

	invoice.set_missing_values()
	invoice.calculate_taxes_and_totals()
	invoice.save(ignore_permissions=True)

	return {
		"received_total": received_total,
		"embedded_total": sum(embedded_by_mode.values()),
		"excess_by_mode": excess_by_mode,
		"excess_registers_by_mode": excess_registers_by_mode,
	}


def _finalize_mpesa_reconciliation(invoice, embed_summary: dict | None = None) -> list[dict]:
	"""Post-submit phase of the hybrid M-Pesa flow.

	Consumes every still-pending `Mpesa C2B Payment Register` row recorded on
	the now-submitted invoice (marking it used, WITHOUT letting its own
	`submit_payment` hook mint a duplicate Payment Entry -- the paid portion is
	already embedded on the invoice), then creates a single unallocated
	`Payment Entry` per mode for any overpaid excess. That excess is a reusable
	customer credit surfaced via klik_pos's unallocated-payments screen, never
	folded into the invoice as cash "change" (M-Pesa hands back no physical
	change).

	`embed_summary` is the dict returned by `_embed_mpesa_payments`; the excess
	amounts and the register rows that carried them come from it. When omitted
	(no embed ran), only consumption happens and no excess PE is created.

	Must only be called on a submitted invoice (docstatus=1): a Payment Entry
	credit needs a submitted party context.
	"""
	from frappe_mpsa_payments.frappe_mpsa_payments.api.payment_entry import create_payment_entry

	if invoice.docstatus != 1:
		frappe.throw(
			_("Cannot finalize Mpesa reconciliation for {0}: invoice is not submitted (docstatus={1}).").format(
				invoice.name, invoice.docstatus
			)
		)

	summary = embed_summary or {}
	excess_by_mode = summary.get("excess_by_mode") or {}
	excess_registers_by_mode = summary.get("excess_registers_by_mode") or {}

	# 1. Consume the register rows without minting a per-row Payment Entry:
	#    submit_payment=0 makes the register `before_submit` skip
	#    create_payment_entry, and `on_submit._reconcile_payment` early-returns
	#    on an empty payment_entry -- so submitting is side-effect-free beyond
	#    marking the row used.
	pending_rows = _pending_mpesa_rows(invoice)
	for child in pending_rows:
		row = frappe.get_doc("Mpesa C2B Payment Register", child.mpesa_c2b_payment_register)
		row.customer = invoice.customer
		row.mode_of_payment = child.mode_of_payment
		if not row.company:
			row.company = invoice.company
		row.submit_payment = 0
		row.save(ignore_permissions=True)
		row.submit()

	# 2. Create one unallocated excess credit Payment Entry per mode.
	results = []
	for mode, excess in excess_by_mode.items():
		excess = flt(excess)
		if excess <= 0:
			continue
		pe = create_payment_entry(
			invoice.company,
			invoice.customer,
			excess,
			invoice.currency,
			mode,
			party_type="Customer",
			reference_no=f"Mpesa excess credit for {invoice.name}",
			posting_date=invoice.posting_date,
			submit=1,
		)

		# Link the credit PE back onto the overflowing traceability rows.
		# custom_mpesa_reconciled_payments is read-only on the submitted parent,
		# so write the child column directly.
		for register_name in excess_registers_by_mode.get(mode, []):
			child = next(
				(
					c
					for c in invoice.get("custom_mpesa_reconciled_payments") or []
					if c.mpesa_c2b_payment_register == register_name
				),
				None,
			)
			if child:
				frappe.db.set_value(
					"POS Mpesa Reconciled Payment", child.name, "excess_payment_entry", pe.name
				)

		results.append(
			{
				"mode_of_payment": mode,
				"payment_entry": pe.name,
				"excess_amount": excess,
				"unallocated_amount": flt(pe.unallocated_amount),
			}
		)

	invoice.reload()
	return results

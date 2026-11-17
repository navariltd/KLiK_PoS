"""Which Payment Entries settled an invoice - read once, shared by every surface.

M-Pesa money arrives on the invoice as advances from Payment Entries, so the payments
table shows the mode with no amount. The invoice list, the detail page and the receipt
all need the money, the receipt number and the phone; this is the one place they get it.
"""

import frappe
from frappe.utils import flt


def advance_payment_rows(invoice_names) -> dict:
	"""{invoice: [{mode_of_payment, paid_to, amount, reference_no, phone_number, payment_entry}]}.

	Only invoices that have at least one allocated Payment Entry advance appear as keys, so
	callers can fall back to the payments table with a plain .get().

	A Payment Entry need not carry a Mode of Payment - one made outside the POS (a customer
	advance an accountant recorded) usually has none. Such a row keeps mode_of_payment
	blank and carries paid_to, the account the money went to, for mode_label() to show
	instead. No mode is guessed for it: a mode is what the Closing Shift counts, the
	closing entry's rows are Links to Mode of Payment, and money the till never handled is
	not the cashier's to count.

	pos_opening_entry is the shift the Payment Entry itself was stamped with (M-Pesa
	receipts taken at the till are). The closing screens count an advance only when it is
	stamped with the shift being closed.

	Only submitted entries on a live invoice count. ERPNext leaves the advance rows behind
	on a cancelled invoice - it deletes them only when a single payment is unlinked - so
	without the docstatus filters a cancelled sale would still read as paid by M-Pesa.
	"""
	if not invoice_names:
		return {}
	# The phone number is a custom field installed by frappe_mpsa_payments' own patch.
	# Selecting it on a site that has not got it would turn every invoice list and detail
	# page into a 500, so the whole surface degrades to "no phone" instead.
	phone = (
		"pe.custom_mpesa_phone_number AS phone_number"
		if frappe.db.has_column("Payment Entry", "custom_mpesa_phone_number")
		else "NULL AS phone_number"
	)
	stamped_shift = (
		"pe.custom_pos_opening_entry AS pos_opening_entry"
		if frappe.db.has_column("Payment Entry", "custom_pos_opening_entry")
		else "NULL AS pos_opening_entry"
	)
	rows = frappe.db.sql(
		f"""
		SELECT adv.parent, adv.reference_name AS payment_entry, adv.allocated_amount AS amount,
			pe.mode_of_payment, pe.paid_to, pe.reference_no, {phone}, {stamped_shift}
		FROM `tabSales Invoice Advance` adv
		INNER JOIN `tabPayment Entry` pe ON pe.name = adv.reference_name
		INNER JOIN `tabSales Invoice` si ON si.name = adv.parent
		WHERE adv.parenttype = 'Sales Invoice'
			AND adv.parent IN %(names)s
			AND adv.reference_type = 'Payment Entry'
			AND adv.allocated_amount > 0
			AND pe.docstatus = 1
			AND si.docstatus != 2
		ORDER BY adv.parent, adv.idx
		""",
		{"names": tuple(invoice_names)},
		as_dict=True,
	)
	out: dict = {}
	for row in rows:
		out.setdefault(row.parent, []).append(
			{
				"mode_of_payment": row.mode_of_payment,
				"paid_to": row.paid_to,
				"amount": flt(row.amount),
				"reference_no": row.reference_no,
				"phone_number": row.phone_number,
				"payment_entry": row.payment_entry,
				"pos_opening_entry": row.pos_opening_entry or None,
			}
		)
	return out


def merge_payment_rows(sip_rows: list, advance_rows: list) -> list:
	"""Payments-table rows plus advance rows, without the placeholder zero rows.

	The zero-amount row exists to satisfy ERPNext and to carry the mode; once the money
	for that mode is here from an advance, showing 'Mpesa 0.00' beside 'Mpesa 450.00'
	would read as two payments.
	"""
	if not advance_rows:
		return list(sip_rows)
	advanced_modes = {r["mode_of_payment"] for r in advance_rows if r.get("mode_of_payment")}
	kept = [r for r in sip_rows if not (flt(r.get("amount")) == 0 and r.get("mode_of_payment") in advanced_modes)]
	return kept + list(advance_rows)


def mode_label(payment_rows: list) -> str:
	"""The 'Cash/Mpesa' label a list shows for how an invoice was paid.

	Distinct modes in first-seen order: one row per receipt means several rows share a
	mode, and 'Mpesa/Mpesa/Mpesa' would no longer match the Mode of Payment filter. A row
	with no mode - a Payment Entry need not have one - shows the account the money went to
	(paid_to) instead of crashing the join, and an invoice with nothing to show reads as '-'.
	"""
	modes = list(dict.fromkeys(row.get("mode_of_payment") or row.get("paid_to") for row in payment_rows))
	modes = [m for m in modes if m]
	return "/".join(modes) if modes else "-"


def payment_details(invoice) -> list:
	"""Every payment on the invoice with what identifies it, for the invoice page.

	The till's own rows (money taken at checkout - a refund on a return is negative), then
	each Payment Entry that paid it. A submitted invoice's references are authoritative: an
	advance is reconciled into them on submit, and payment terms may split one entry over
	several rows, so they are summed per entry. An advance with no reference row (a draft, or
	a site that keeps advances unreconciled) counts as allocated. A cancelled invoice lists no
	entries: ERPNext leaves its advance rows behind, and the entry may now pay another sale.
	"""
	rows = _till_rows(invoice)
	if invoice.docstatus == 2:
		return rows
	fields = _entry_fields()
	referenced = frappe.db.sql(
		f"""
		SELECT SUM(ref.allocated_amount) AS amount, {fields}
		FROM `tabPayment Entry Reference` ref
		INNER JOIN `tabPayment Entry` pe ON pe.name = ref.parent
		WHERE ref.reference_doctype = 'Sales Invoice' AND ref.reference_name = %(inv)s
			AND ref.allocated_amount != 0 AND pe.docstatus = 1
		GROUP BY pe.name
		ORDER BY pe.posting_date, pe.creation
		""",
		{"inv": invoice.name},
		as_dict=True,
	)
	advanced = frappe.db.sql(
		f"""
		SELECT SUM(adv.allocated_amount) AS amount, {fields}
		FROM `tabSales Invoice Advance` adv
		INNER JOIN `tabPayment Entry` pe ON pe.name = adv.reference_name
		WHERE adv.parenttype = 'Sales Invoice' AND adv.parent = %(inv)s
			AND adv.reference_type = 'Payment Entry' AND adv.allocated_amount != 0 AND pe.docstatus = 1
		GROUP BY pe.name
		ORDER BY pe.posting_date, pe.creation
		""",
		{"inv": invoice.name},
		as_dict=True,
	)
	seen = {e.payment_entry for e in referenced}
	entries = [e for e in advanced if e.payment_entry not in seen] + referenced
	entries.sort(key=lambda e: (str(e.posting_date or ""), e.payment_entry))
	return rows + [_entry_row(e) for e in entries]


def _till_rows(invoice) -> list:
	"""The payments table. A row keeps what was tendered; the change handed back comes off the
	row it left through - the Cash row first, else the one paying into the change account - as
	payment._change_given_by_mode does, so the rows add up to what the sale kept."""
	payments = [p for p in invoice.get("payments") or [] if flt(p.amount) != 0]
	change = flt(invoice.get("change_amount"))
	giver = None
	if change > 0:
		change_account = invoice.get("account_for_change_amount")
		giver = next((p for p in payments if p.get("type") == "Cash"), None) or next(
			(p for p in payments if change_account and p.get("account") == change_account), None
		)
	rows = []
	for p in payments:
		row = {
			"source": "till",
			"mode_of_payment": p.mode_of_payment,
			"amount": flt(p.amount),
			"reference_no": p.get("reference_no") or None,
			"remarks": p.get("custom_reference_text") or None,
		}
		if p is giver:
			row.update({"amount": flt(p.amount) - change, "tendered": flt(p.amount), "change": change})
		rows.append(row)
	return rows


def _entry_fields() -> str:
	optional = {"phone_number": "custom_mpesa_phone_number", "receipt_number": "custom_mpesa_receipt_number"}
	extra = ", ".join(
		f"MAX(pe.{column}) AS {alias}" if frappe.db.has_column("Payment Entry", column) else f"NULL AS {alias}"
		for alias, column in optional.items()
	)
	return f"""pe.name AS payment_entry, MAX(pe.mode_of_payment) AS mode_of_payment, MAX(pe.paid_to) AS paid_to,
		MAX(pe.posting_date) AS posting_date, MAX(pe.reference_no) AS reference_no,
		MAX(pe.reference_date) AS reference_date, MAX(pe.clearance_date) AS clearance_date,
		MAX(pe.bank_account) AS bank_account, MAX(pe.custom_remarks) AS custom_remarks,
		MAX(pe.remarks) AS remarks, {extra}"""


def _entry_row(e) -> dict:
	return {
		"source": "payment_entry",
		"payment_entry": e.payment_entry,
		"mode_of_payment": e.mode_of_payment,
		"paid_to": e.paid_to,
		"amount": flt(e.amount),
		"posting_date": e.posting_date,
		# Cheque/Reference No; an M-Pesa entry's is its receipt number.
		"reference_no": e.reference_no or e.receipt_number or None,
		"reference_date": e.reference_date,
		"clearance_date": e.clearance_date,
		"bank_account": e.bank_account,
		"phone_number": e.phone_number,
		# ERPNext writes its own remarks unless someone ticked Custom Remarks and typed them.
		"remarks": e.remarks if e.custom_remarks else None,
	}

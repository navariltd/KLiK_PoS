import frappe
from frappe.utils import flt, getdate, nowdate

from klik_pos.klik_pos.utils import get_current_pos_profile


def _get_ar_execute():
	"""ERPNext's Accounts Receivable report `execute`.

	Imported lazily so importing this module never depends on erpnext being loaded.
	"""
	from erpnext.accounts.report.accounts_receivable.accounts_receivable import execute

	return execute


def _new_customer_entry():
	return {
		"customer": "",
		"customer_name": "",
		"customer_group": "",
		"total_invoiced": 0.0,
		"total_paid": 0.0,
		"outstanding": 0.0,
		"bucket_current": 0.0,
		"bucket_0_30": 0.0,
		"bucket_31_60": 0.0,
		"bucket_61_90": 0.0,
		"bucket_90_plus": 0.0,
		"unallocated_advance": 0.0,
		"last_payment": None,
		"invoices": [],
	}


def _group_receivable_rows(rows, as_of_date, statuses=None):
	"""Group ERPNext AR report rows into one entry per customer, invoices nested.

	Summary figures and the nested invoice rows come from the same pass over the same
	report rows, so the two can never disagree — the drill-down always adds up to the
	row it was expanded from.
	"""
	statuses = statuses or {}
	as_of = getdate(as_of_date)
	grouped = {}

	for row in rows or []:
		party = row.get("party")
		if not party:
			# The report appends a totals row carrying no party.
			continue

		entry = grouped.setdefault(party, _new_customer_entry())
		entry["customer"] = party
		entry["customer_name"] = row.get("customer_name") or party
		entry["customer_group"] = row.get("customer_group") or ""

		invoiced = flt(row.get("invoiced") or 0)
		outstanding = flt(row.get("outstanding") or 0)

		entry["total_invoiced"] = flt(entry["total_invoiced"] + invoiced, 2)
		entry["total_paid"] = flt(entry["total_paid"] + flt(row.get("paid") or 0), 2)
		entry["outstanding"] = flt(entry["outstanding"] + outstanding, 2)
		# range0 is ERPNext's "<0" column — not yet due — so it gets its own bucket
		# instead of inflating 0-30. range1 is the real 0-30.
		entry["bucket_current"] = flt(entry["bucket_current"] + flt(row.get("range0") or 0), 2)
		entry["bucket_0_30"] = flt(entry["bucket_0_30"] + flt(row.get("range1") or 0), 2)
		entry["bucket_31_60"] = flt(entry["bucket_31_60"] + flt(row.get("range2") or 0), 2)
		entry["bucket_61_90"] = flt(entry["bucket_61_90"] + flt(row.get("range3") or 0), 2)
		entry["bucket_90_plus"] = flt(
			entry["bucket_90_plus"] + flt(row.get("range4") or 0) + flt(row.get("range5") or 0), 2
		)

		voucher_type = row.get("voucher_type")

		if voucher_type in ("Payment Entry", "Journal Entry"):
			posting_date = getdate(row["posting_date"])
			if not entry["last_payment"] or posting_date > getdate(entry["last_payment"]):
				entry["last_payment"] = posting_date
			if outstanding < 0:
				entry["unallocated_advance"] = flt(entry["unallocated_advance"] + -outstanding, 2)

		elif voucher_type == "Sales Invoice" and outstanding > 0:
			due_date = getdate(row["due_date"]) if row.get("due_date") else getdate(row["posting_date"])
			entry["invoices"].append(
				{
					"name": row["voucher_no"],
					"posting_date": row["posting_date"],
					"grand_total": flt(invoiced, 2),
					# Derived from the report row, not the invoice doc, so it stays
					# as-of-date consistent with the rest of the row.
					"paid": flt(invoiced - outstanding, 2),
					"outstanding": flt(outstanding, 2),
					"due_date": row.get("due_date"),
					"days_overdue": max(0, (as_of - due_date).days),
					"status": statuses.get(row["voucher_no"]),
				}
			)

	result = [e for e in grouped.values() if e["outstanding"] or e["unallocated_advance"]]
	for entry in result:
		# Oldest due first — the frontend's oldest-first allocation consumes this order
		# directly rather than re-deriving it.
		entry["invoices"].sort(key=lambda i: getdate(i["due_date"] or i["posting_date"]))
	result.sort(key=lambda e: e["outstanding"], reverse=True)
	return result


@frappe.whitelist()
def get_customer_receivables(as_of_date=None):
	"""One row per customer with a receivable balance, with their open invoices nested.

	Delegates to ERPNext's Accounts Receivable report engine (Payment Ledger Entry based)
	rather than a bespoke Sales Invoice query, so credit notes, returns and journal
	entries net into the totals automatically and the figures match the TRANSACTION
	HISTORY page in cecypo_frappe_reports.
	"""
	try:
		frappe.has_permission("Sales Invoice", "read", throw=True)

		pos_profile = get_current_pos_profile()
		as_of_date = getdate(as_of_date or nowdate())

		_columns, data, *_rest = _get_ar_execute()(
			{
				"company": pos_profile.company,
				"report_date": as_of_date,
				"party_type": "Customer",
			}
		)

		voucher_nos = [
			row["voucher_no"]
			for row in (data or [])
			if row.get("voucher_type") == "Sales Invoice" and flt(row.get("outstanding") or 0) > 0
		]
		statuses = {}
		if voucher_nos:
			statuses = {
				d.name: d.status
				for d in frappe.db.get_all(
					"Sales Invoice",
					filters={"name": ["in", voucher_nos]},
					fields=["name", "status"],
				)
			}

		return {
			"success": True,
			"as_of_date": str(as_of_date),
			"currency": frappe.db.get_value("Company", pos_profile.company, "default_currency"),
			"data": _group_receivable_rows(data, as_of_date, statuses),
		}
	except Exception as e:
		frappe.log_error(
			title="Get Customer Receivables Error",
			message=frappe.get_traceback(),
		)
		return {"success": False, "error": str(e)}

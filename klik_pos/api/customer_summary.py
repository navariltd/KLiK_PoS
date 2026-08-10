"""One customer's account position, for the stat cards on the customer detail page.

Deliberately NOT sourced from klik_pos.api.sales_invoice.get_sales_invoices: that endpoint is
POS till history (it filters on custom_pos_opening_entry) and matches customers with a LIKE
substring. Cards built on it under-report a customer who also has back-office invoices, which
is most of them.
"""

import frappe
from frappe import _
from frappe.utils import flt

from klik_pos.api.receivables import get_customer_receivables
from klik_pos.klik_pos.utils import get_current_pos_profile


def _default_company():
	"""Resolve the company for the currency figure, POS profile first.

	Falls back to the user's default company when no POS profile resolves for the current
	session (e.g. no POS Opening Entry and no POS Profile User row) — get_current_pos_profile
	throws in that case rather than returning None.
	"""
	try:
		pos_profile = get_current_pos_profile()
		if pos_profile and pos_profile.company:
			return pos_profile.company
	except Exception:
		pass

	return frappe.defaults.get_user_default("Company")


def _outstanding_for(customer):
	"""Delegate to the AR path so this always agrees with the Statement and the By Customer tab.

	get_customer_receivables never raises (it catches internally and returns
	{"success": False, "error": ...}), and its "data" list is empty for a customer who owes
	nothing — never assume a row exists.
	"""
	response = get_customer_receivables(customer=customer) or {}
	data = response.get("data") or []
	if not data:
		return 0.0
	return flt(data[0].get("outstanding") or 0.0, 2)


@frappe.whitelist()
def get_customer_account_summary(customer):
	"""Return the four figures the customer detail page shows as headline cards.

	All four come from one call so they cannot disagree with each other, and none of them
	depend on whatever filter the user has applied to the invoice table below.
	"""
	if not customer:
		frappe.throw(_("Customer is required."))

	if not frappe.db.exists("Customer", customer):
		frappe.throw(_("Customer {0} not found.").format(customer))

	# get_all applies read permissions; an exact customer filter, never a LIKE.
	rows = frappe.get_all(
		"Sales Invoice",
		filters={"customer": customer, "docstatus": 1},
		fields=["base_grand_total", "is_return"],
	)

	# A return is not an order: it must not inflate the count or drag the average, but its
	# negative total must still reduce revenue. grand_total on a return is already negative,
	# so summing every submitted invoice already nets it in — it must not be subtracted again.
	invoice_count = sum(1 for row in rows if not row.is_return)
	net_revenue = flt(sum(flt(row.base_grand_total) for row in rows), 2)
	avg_order_value = flt(net_revenue / invoice_count, 2) if invoice_count else 0.0

	company = _default_company()

	return {
		"invoice_count": invoice_count,
		"net_revenue": net_revenue,
		"avg_order_value": avg_order_value,
		"outstanding": _outstanding_for(customer),
		"currency": frappe.get_cached_value("Company", company, "default_currency") if company else None,
	}

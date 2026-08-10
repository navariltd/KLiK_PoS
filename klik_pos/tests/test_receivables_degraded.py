import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, nowdate
from unittest.mock import patch

from klik_pos.api import receivables

COMPANY = "Dev Co"
ITEM = "Consulting"


def _make_customer(name):
	if frappe.db.exists("Customer", name):
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": name,
			"customer_type": "Individual",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _make_invoice(customer, rate, qty=1, do_submit=True):
	si = frappe.new_doc("Sales Invoice")
	si.customer = customer
	si.company = COMPANY
	si.is_pos = 0
	si.append(
		"items",
		{
			"item_code": ITEM,
			"qty": qty,
			"rate": rate,
		},
	)
	si.insert(ignore_permissions=True)
	if do_submit:
		si.submit()
	return si


def _make_advance_payment(customer, amount):
	"""A standalone Receive with no invoice references — the whole amount lands as an
	unallocated advance, which the fallback (Sales Invoice only) cannot see."""
	receivable_account, cash_account, currency = frappe.db.get_value(
		"Company", COMPANY, ["default_receivable_account", "default_cash_account", "default_currency"]
	)
	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Receive"
	pe.party_type = "Customer"
	pe.party = customer
	pe.company = COMPANY
	pe.posting_date = nowdate()
	pe.paid_from = receivable_account
	pe.paid_to = cash_account
	pe.paid_amount = amount
	pe.received_amount = amount
	pe.source_exchange_rate = 1
	pe.target_exchange_rate = 1
	pe.paid_from_account_currency = currency
	pe.paid_to_account_currency = currency
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe


def _raises_permission_error(filters):
	raise frappe.PermissionError("Journal Entry")


def _raises_value_error(filters):
	raise ValueError("boom")


class TestReceivablesDegradedFallback(FrappeTestCase):
	"""Fixture: `customer` has two submitted, non-return invoices of 100 and 50 (150 total
	outstanding) plus a standalone advance payment of 60 with no invoice reference — an
	unallocated advance the fallback path cannot see, mirroring the real 6,339 gap on
	"Commercial Customer" on dev.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		suffix = frappe.generate_hash(length=8)
		cls.customer = _make_customer(f"Degraded Fallback Customer {suffix}")

		cls.invoice_one = _make_invoice(cls.customer, 100)
		cls.invoice_two = _make_invoice(cls.customer, 50)

		cls.advance_payment = _make_advance_payment(cls.customer, 60)

	def test_permission_error_falls_back_instead_of_returning_failure(self):
		"""A user without Journal Entry read must still see what is owed. Before this, the
		whole call failed and the Receive modal silently took payments on account."""
		with patch.object(
			receivables, "_get_ar_execute",
			return_value=_raises_permission_error,
		):
			result = receivables.get_customer_receivables(customer=self.customer)

		self.assertTrue(result["success"])
		self.assertTrue(result["degraded"])
		self.assertIn("Journal Entry", result["degraded_reason"])
		self.assertTrue(result["data"], "the fallback must still return the customer's position")

	def test_the_fallback_totals_gross_of_advances(self):
		"""Documents the known difference rather than hiding it: the fallback reads
		outstanding_amount per invoice and cannot net an unallocated advance."""
		with patch.object(receivables, "_get_ar_execute", return_value=_raises_permission_error):
			degraded = receivables.get_customer_receivables(customer=self.customer)

		self.assertEqual(degraded["data"][0]["outstanding"], 150.0)
		self.assertEqual(degraded["data"][0]["unallocated_advance"], 0.0)

	def test_per_invoice_outstanding_matches_the_ar_path(self):
		"""Allocation correctness is the thing that must NOT degrade — the Receive modal
		allocates per invoice, so these figures have to agree with the ledger path."""
		healthy = receivables.get_customer_receivables(customer=self.customer)
		with patch.object(receivables, "_get_ar_execute", return_value=_raises_permission_error):
			degraded = receivables.get_customer_receivables(customer=self.customer)

		healthy_by_name = {
			inv["name"]: flt(inv["outstanding"], 2) for inv in healthy["data"][0]["invoices"]
		}
		degraded_by_name = {
			inv["name"]: flt(inv["outstanding"], 2) for inv in degraded["data"][0]["invoices"]
		}
		self.assertEqual(healthy_by_name, degraded_by_name)

	def test_a_non_permission_error_is_not_swallowed_as_degraded(self):
		"""A genuine bug in the AR engine must still surface as a failure. Reporting it as
		'degraded' would present broken figures as merely approximate."""
		with patch.object(receivables, "_get_ar_execute", return_value=_raises_value_error):
			result = receivables.get_customer_receivables(customer=self.customer)
		self.assertFalse(result["success"])

	def test_the_fallback_still_requires_sales_invoice_read(self):
		"""The fallback is narrower, not unguarded. A caller who cannot read Sales Invoice
		gets nothing — it must not become a way around permissions."""
		with patch.object(
			frappe, "has_permission", side_effect=frappe.PermissionError("Sales Invoice")
		):
			with patch.object(receivables, "_get_ar_execute", return_value=_raises_permission_error):
				result = receivables.get_customer_receivables(customer=self.customer)
		self.assertFalse(result["success"])

	def test_the_healthy_path_reports_degraded_false(self):
		result = receivables.get_customer_receivables(customer=self.customer)
		self.assertTrue(result["success"])
		self.assertFalse(result["degraded"])
		self.assertIsNone(result["degraded_reason"])

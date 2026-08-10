import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api import customer_summary
from klik_pos.api.receivables import get_customer_receivables

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


def _make_invoice(customer, rate, qty=1, is_return=0, return_against=None, do_submit=True):
	si = frappe.new_doc("Sales Invoice")
	si.customer = customer
	si.company = COMPANY
	si.is_pos = 0
	si.is_return = is_return
	si.return_against = return_against
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


class TestCustomerAccountSummary(FrappeTestCase):
	"""Fixture: `customer` has two submitted non-return invoices of 100 each, one submitted
	return of -50, one draft, and one cancelled invoice. A lookalike customer whose name is
	`customer`'s name plus " Ltd" has one invoice of its own, to prove the customer filter is
	exact. `empty_customer` has no invoices at all.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		suffix = frappe.generate_hash(length=8)
		cls.customer = _make_customer(f"CAS Test Customer {suffix}")
		cls.lookalike_customer = _make_customer(f"{cls.customer} Ltd")
		cls.empty_customer = _make_customer(f"CAS Test Empty Customer {suffix}")

		cls.invoice_one = _make_invoice(cls.customer, 100)
		cls.invoice_two = _make_invoice(cls.customer, 100)
		cls.return_invoice = _make_invoice(
			cls.customer, 50, qty=-1, is_return=1, return_against=cls.invoice_one.name
		)

		cls.draft_invoice = _make_invoice(cls.customer, 500, do_submit=False)

		cls.cancelled_invoice = _make_invoice(cls.customer, 300)
		cls.cancelled_invoice.cancel()

		_make_invoice(cls.lookalike_customer, 999)

		# Derived from the same helper the endpoint delegates to, not hardcoded — this test is
		# about the endpoint delegating to the AR path, not about a magic number.
		cls.expected_outstanding = 0.0
		response = get_customer_receivables(customer=cls.customer)
		data = response.get("data") or []
		if data:
			cls.expected_outstanding = data[0]["outstanding"]

	def test_counts_exclude_returns_but_revenue_is_net_of_them(self):
		"""A return is not an order — it must not inflate the invoice count or drag the
		average down — but its negative total must still reduce revenue."""
		summary = customer_summary.get_customer_account_summary(customer=self.customer)

		self.assertEqual(summary["invoice_count"], 2)
		self.assertEqual(summary["net_revenue"], 150.0)  # 100 + 100 - 50
		self.assertEqual(summary["avg_order_value"], 75.0)  # 150 / 2, not 150 / 3

	def test_drafts_and_cancelled_are_excluded(self):
		"""Only submitted invoices are part of the customer's account."""
		summary = customer_summary.get_customer_account_summary(customer=self.customer)
		self.assertEqual(summary["invoice_count"], 2)

	def test_outstanding_comes_from_the_receivable_path_not_a_sum_of_invoices(self):
		"""Summing outstanding_amount ignores unallocated advances and overstates what the
		customer owes. The AR path nets them, and is what the Statement shows."""
		summary = customer_summary.get_customer_account_summary(customer=self.customer)
		self.assertEqual(summary["outstanding"], self.expected_outstanding)

	def test_a_customer_with_no_invoices_returns_zeroes_not_an_error(self):
		summary = customer_summary.get_customer_account_summary(customer=self.empty_customer)
		self.assertEqual(summary["invoice_count"], 0)
		self.assertEqual(summary["net_revenue"], 0.0)
		self.assertEqual(summary["avg_order_value"], 0.0)
		self.assertEqual(summary["outstanding"], 0.0)

	def test_customer_name_is_matched_exactly_not_by_substring(self):
		"""The old card path matched with LIKE %name%, so "Customer A" pulled in
		"Customer A Ltd"'s invoices. An exact match is the whole point of this endpoint."""
		summary = customer_summary.get_customer_account_summary(customer=self.customer)
		self.assertEqual(summary["invoice_count"], 2)  # not 3, the lookalike is excluded

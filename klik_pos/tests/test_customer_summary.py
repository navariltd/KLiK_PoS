from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

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


def _make_advance_payment(customer, amount):
	"""A standalone Receive with no invoice references — the whole amount lands as an
	unallocated advance, which is exactly what a naive sum(outstanding_amount) cannot see."""
	receivable_account, cash_account, currency = frappe.db.get_value(
		"Company", COMPANY, ["default_receivable_account", "default_cash_account", "default_currency"]
	)
	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Receive"
	pe.party_type = "Customer"
	pe.party = customer
	pe.company = COMPANY
	pe.posting_date = frappe.utils.nowdate()
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


def _naive_sum_outstanding(customer):
	"""The wrong implementation this endpoint replaces — summing outstanding_amount directly,
	blind to unallocated advances. Used only to prove the fixture's AR-netted figure actually
	diverges from it."""
	total = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(outstanding_amount), 0)
		FROM `tabSales Invoice`
		WHERE customer = %s AND docstatus = 1
		""",
		(customer,),
	)[0][0]
	return flt(total, 2)


class TestCustomerAccountSummary(FrappeTestCase):
	"""Fixture: `customer` has two submitted non-return invoices of 100 each, one submitted
	return of -50, one draft, and one cancelled invoice, plus a standalone advance payment of
	60 with no invoice reference — an unallocated advance. A lookalike customer whose name is
	`customer`'s name plus " Ltd" has one invoice of its own, to prove the customer filter is
	exact. `empty_customer` has no invoices at all.

	The advance payment matters: without it, every submitted invoice here is unpaid, so a naive
	sum(outstanding_amount) and the AR-netted figure agree by coincidence and a bug that reads
	one instead of the other cannot be caught. The 60 advance makes them diverge (150 naive vs
	90 netted), the same shape as the 6,339 gap on the real "Commercial Customer" on dev.
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

		cls.advance_payment = _make_advance_payment(cls.customer, 60)

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
		naive_sum = _naive_sum_outstanding(self.customer)

		self.assertEqual(summary["outstanding"], self.expected_outstanding)
		# The fixture's advance payment must make these genuinely different figures — if they
		# ever coincide, this test can no longer tell a correct implementation from one that
		# just sums outstanding_amount, which is the bug this endpoint exists to fix.
		self.assertNotEqual(
			self.expected_outstanding,
			naive_sum,
			"fixture produced no divergence between the AR-netted and naive outstanding figures "
			"— this test is blind without one",
		)

	def test_outstanding_is_none_when_it_cannot_be_determined(self):
		"""get_customer_receivables never raises — a permission failure or a broken AR report
		comes back as {"success": False, ...}, with no "data" key at all. That must not read as
		"customer owes nothing": a confident wrong zero on this card is worse than an unknown
		value. Task 2 renders None as a dash rather than a number."""
		with patch.object(
			customer_summary,
			"get_customer_receivables",
			return_value={"success": False, "error": "boom"},
		):
			summary = customer_summary.get_customer_account_summary(customer=self.customer)
		self.assertIsNone(summary["outstanding"])

	def test_a_customer_with_no_invoices_returns_zeroes_not_an_error(self):
		summary = customer_summary.get_customer_account_summary(customer=self.empty_customer)
		self.assertEqual(summary["invoice_count"], 0)
		self.assertEqual(summary["net_revenue"], 0.0)
		self.assertEqual(summary["avg_order_value"], 0.0)
		# Genuinely nothing owed (the AR path succeeds and returns no row for this customer) is
		# a fact, not a failure — this must stay 0.0, not None.
		self.assertEqual(summary["outstanding"], 0.0)

	def test_customer_name_is_matched_exactly_not_by_substring(self):
		"""The old card path matched with LIKE %name%, so "Customer A" pulled in
		"Customer A Ltd"'s invoices. An exact match is the whole point of this endpoint."""
		summary = customer_summary.get_customer_account_summary(customer=self.customer)
		self.assertEqual(summary["invoice_count"], 2)  # not 3, the lookalike is excluded

	def test_currency_matches_the_resolved_company(self):
		summary = customer_summary.get_customer_account_summary(customer=self.customer)
		self.assertEqual(summary["currency"], frappe.get_cached_value("Company", COMPANY, "default_currency"))

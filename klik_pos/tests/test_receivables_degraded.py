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


def _make_invoice(customer, rate, qty=1, do_submit=True, posting_date=None, due_date=None):
	si = frappe.new_doc("Sales Invoice")
	si.customer = customer
	si.company = COMPANY
	si.is_pos = 0
	if posting_date:
		# Sales Invoice silently resets posting_date to today on save unless this is set —
		# it's the "Edit Posting Date and Time" checkbox.
		si.set_posting_time = 1
		si.posting_date = posting_date
	if due_date:
		si.due_date = due_date
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


def _make_partial_payment(customer, invoice, amount):
	"""A Receive allocated against `invoice` only (never any pre-existing document), leaving
	that invoice's outstanding_amount below its grand_total. Two fully-unpaid invoices can't
	distinguish an implementation that reads outstanding_amount from one that reads
	grand_total by mistake — both fields are numerically identical when nothing has been
	paid. This closes that gap."""
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
	pe.append(
		"references",
		{
			"reference_doctype": "Sales Invoice",
			"reference_name": invoice.name,
			"total_amount": invoice.grand_total,
			"outstanding_amount": invoice.outstanding_amount,
			"allocated_amount": amount,
		},
	)
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe


def _raises_permission_error(filters):
	raise frappe.PermissionError("Journal Entry")


def _raises_value_error(filters):
	raise ValueError("boom")


class TestReceivablesDegradedFallback(FrappeTestCase):
	"""Fixture: `customer` has two submitted, non-return invoices — 100 fully unpaid and 50
	partially paid down by 20 (leaving 30 outstanding, distinct from its 50 grand_total) —
	plus a standalone advance payment of 60 with no invoice reference. Gross outstanding
	(Sales Invoice only) is 100 + 30 = 130.0; AR-netted is 130 - 60 = 70.0. The gap is the
	unallocated advance, the same shape as the real 6,339 gap on "Commercial Customer" on
	dev. All documents belong to a customer created fresh for this test class — nothing
	pre-existing is read or written.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		suffix = frappe.generate_hash(length=8)
		cls.customer = _make_customer(f"Degraded Fallback Customer {suffix}")

		cls.invoice_one = _make_invoice(cls.customer, 100)
		cls.invoice_two = _make_invoice(cls.customer, 50)
		cls.partial_payment = _make_partial_payment(cls.customer, cls.invoice_two, 20)
		cls.invoice_two.reload()

		cls.advance_payment = _make_advance_payment(cls.customer, 60)

		# A second, isolated customer: one invoice whose due_date is far from its
		# posting_date, to pin the ageing basis (Important 1). Kept separate from
		# `cls.customer` so it doesn't perturb the total/per-invoice assertions above.
		cls.ageing_customer = _make_customer(f"Degraded Fallback Ageing Customer {suffix}")
		cls.ageing_invoice = _make_invoice(
			cls.ageing_customer,
			200,
			posting_date=add_days(nowdate(), -40),
			due_date=add_days(nowdate(), -10),
		)

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

		self.assertEqual(degraded["data"][0]["outstanding"], 130.0)  # 100 + 30, gross of the 60 advance
		self.assertEqual(degraded["data"][0]["unallocated_advance"], 0.0)

	def test_the_fallback_reads_outstanding_amount_not_grand_total(self):
		"""The partially-paid invoice is the whole point of this fixture: its
		outstanding_amount (30.0) and grand_total (50.0) differ, so a fallback that
		accidentally read grand_total instead of outstanding_amount cannot hide behind a
		fully-unpaid invoice where the two are numerically identical."""
		with patch.object(receivables, "_get_ar_execute", return_value=_raises_permission_error):
			degraded = receivables.get_customer_receivables(customer=self.customer)

		by_name = {inv["name"]: inv for inv in degraded["data"][0]["invoices"]}
		partial = by_name[self.invoice_two.name]
		self.assertEqual(partial["outstanding"], 30.0)
		self.assertEqual(partial["grand_total"], 50.0)
		self.assertNotEqual(partial["outstanding"], partial["grand_total"])

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
		self.assertEqual(healthy_by_name[self.invoice_two.name], 30.0)

	def test_bucket_assignment_matches_the_ar_path_when_due_date_differs_from_posting_date(self):
		"""Pins the ageing basis. Without ageing_based_on='Due Date' and
		calculate_ageing_with='Report Date' on the AR filters, the healthy path ages by
		posting_date as of today while the fallback ages by due_date as of as_of_date — an
		invoice posted 40 days ago but due only 10 days ago would then land in bucket_31_60
		on the healthy path and bucket_0_30 on the fallback, silently disagreeing. Every
		other fixture in this file has due_date == posting_date, which is exactly why that
		would have gone unnoticed."""
		healthy = receivables.get_customer_receivables(customer=self.ageing_customer)
		with patch.object(receivables, "_get_ar_execute", return_value=_raises_permission_error):
			degraded = receivables.get_customer_receivables(customer=self.ageing_customer)

		buckets = ("bucket_current", "bucket_0_30", "bucket_31_60", "bucket_61_90", "bucket_90_plus")

		def bucket_for(entry):
			nonzero = [b for b in buckets if entry[b]]
			self.assertEqual(len(nonzero), 1, f"expected exactly one non-zero bucket, got {nonzero}")
			return nonzero[0]

		healthy_bucket = bucket_for(healthy["data"][0])
		degraded_bucket = bucket_for(degraded["data"][0])
		self.assertEqual(healthy_bucket, degraded_bucket)
		# Due 10 days ago, not posted 40 days ago — proves both paths actually used due_date.
		self.assertEqual(healthy_bucket, "bucket_0_30")

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

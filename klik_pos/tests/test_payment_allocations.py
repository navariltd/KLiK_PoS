from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.payment import _build_allocation_rows


def _invoice(customer="ACME", docstatus=1, grand_total=1000.0, outstanding_amount=1000.0):
	return SimpleNamespace(
		customer=customer,
		docstatus=docstatus,
		grand_total=grand_total,
		outstanding_amount=outstanding_amount,
	)


class TestBuildAllocationRows(FrappeTestCase):
	"""Guards for receiving one payment across several invoices from the By Customer tab."""

	def test_builds_one_reference_row_per_invoice(self):
		invoices = {
			"INV-001": _invoice(grand_total=600.0, outstanding_amount=600.0),
			"INV-002": _invoice(grand_total=400.0, outstanding_amount=400.0),
		}
		rows = _build_allocation_rows(
			[
				{"sales_invoice": "INV-001", "allocated_amount": 600.0},
				{"sales_invoice": "INV-002", "allocated_amount": 400.0},
			],
			"ACME",
			1000.0,
			invoices,
		)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0]["reference_doctype"], "Sales Invoice")
		self.assertEqual(rows[0]["reference_name"], "INV-001")
		self.assertEqual(rows[0]["allocated_amount"], 600.0)
		self.assertEqual(rows[0]["total_amount"], 600.0)
		self.assertEqual(rows[0]["outstanding_amount"], 600.0)

	def test_partial_allocation_is_allowed(self):
		invoices = {"INV-001": _invoice(outstanding_amount=1000.0)}
		rows = _build_allocation_rows(
			[{"sales_invoice": "INV-001", "allocated_amount": 250.0}], "ACME", 250.0, invoices
		)
		self.assertEqual(rows[0]["allocated_amount"], 250.0)

	def test_remainder_below_amount_is_allowed(self):
		# Paying 1000 against 400 of outstanding leaves 600 unallocated — that is the advance.
		invoices = {"INV-001": _invoice(outstanding_amount=400.0)}
		rows = _build_allocation_rows(
			[{"sales_invoice": "INV-001", "allocated_amount": 400.0}], "ACME", 1000.0, invoices
		)
		self.assertEqual(len(rows), 1)

	def test_rejects_allocation_above_invoice_outstanding(self):
		invoices = {"INV-001": _invoice(outstanding_amount=400.0)}
		with self.assertRaises(frappe.ValidationError):
			_build_allocation_rows(
				[{"sales_invoice": "INV-001", "allocated_amount": 500.0}], "ACME", 500.0, invoices
			)

	def test_rejects_total_above_payment_amount(self):
		invoices = {
			"INV-001": _invoice(outstanding_amount=600.0),
			"INV-002": _invoice(outstanding_amount=600.0),
		}
		with self.assertRaises(frappe.ValidationError):
			_build_allocation_rows(
				[
					{"sales_invoice": "INV-001", "allocated_amount": 600.0},
					{"sales_invoice": "INV-002", "allocated_amount": 600.0},
				],
				"ACME",
				1000.0,
				invoices,
			)

	def test_rejects_invoice_belonging_to_another_customer(self):
		invoices = {"INV-001": _invoice(customer="BETA")}
		with self.assertRaises(frappe.ValidationError):
			_build_allocation_rows(
				[{"sales_invoice": "INV-001", "allocated_amount": 100.0}], "ACME", 100.0, invoices
			)

	def test_rejects_draft_invoice(self):
		invoices = {"INV-001": _invoice(docstatus=0)}
		with self.assertRaises(frappe.ValidationError):
			_build_allocation_rows(
				[{"sales_invoice": "INV-001", "allocated_amount": 100.0}], "ACME", 100.0, invoices
			)

	def test_rejects_settled_invoice(self):
		invoices = {"INV-001": _invoice(outstanding_amount=0.0)}
		with self.assertRaises(frappe.ValidationError):
			_build_allocation_rows(
				[{"sales_invoice": "INV-001", "allocated_amount": 100.0}], "ACME", 100.0, invoices
			)

	def test_rejects_zero_allocation(self):
		invoices = {"INV-001": _invoice()}
		with self.assertRaises(frappe.ValidationError):
			_build_allocation_rows(
				[{"sales_invoice": "INV-001", "allocated_amount": 0.0}], "ACME", 100.0, invoices
			)

	def test_rejects_missing_invoice_name(self):
		with self.assertRaises(frappe.ValidationError):
			_build_allocation_rows([{"allocated_amount": 100.0}], "ACME", 100.0, {})

	def test_rejects_unknown_invoice(self):
		with self.assertRaises(frappe.ValidationError):
			_build_allocation_rows(
				[{"sales_invoice": "INV-GHOST", "allocated_amount": 100.0}], "ACME", 100.0, {}
			)

	def test_empty_allocation_list_returns_no_rows(self):
		self.assertEqual(_build_allocation_rows([], "ACME", 100.0, {}), [])

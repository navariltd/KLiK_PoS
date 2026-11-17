"""The invoice page lists each payment with what identifies it: the cheque or reference number
and date, the M-Pesa receipt and phone, the Payment Entry, and any remarks typed on it."""

from unittest.mock import patch

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

from klik_pos.api.mpesa import _allocate_receipts_before_submit
from klik_pos.api.payment_rows import payment_details
from klik_pos.api.sales_invoice import get_invoice_details
from klik_pos.tests.test_mpesa_payment_entry_first import COMPANY, CUSTOMER, MpesaFirstCase


class TestInvoicePaymentDetails(MpesaFirstCase):
	def test_till_rows_carry_their_reference(self):
		invoice = self._draft(rate=100, posting_date=frappe.utils.nowdate())
		invoice.append("payments", {"mode_of_payment": "Cash", "amount": 100, "reference_no": "CHQ-0042"})
		invoice.save(ignore_permissions=True)
		invoice.submit()

		rows = get_invoice_details(invoice.name)["data"]["payment_details"]

		self.assertEqual(rows, [{"source": "till", "mode_of_payment": "Cash", "amount": 100.0, "reference_no": "CHQ-0042", "remarks": None}])

	def test_an_mpesa_advance_shows_its_receipt_phone_and_entry(self):
		invoice = self._draft(rate=100, posting_date=frappe.utils.nowdate())
		invoice = self._record(invoice, self._receipt(100, "254700000901"))
		_allocate_receipts_before_submit(invoice)
		invoice.reload()
		invoice.submit()

		rows = payment_details(invoice)

		self.assertEqual(len(rows), 1, rows)
		row = rows[0]
		self.assertEqual(row["source"], "payment_entry")
		self.assertEqual(row["amount"], 100.0)
		self.assertTrue(row["reference_no"])
		self.assertEqual(row["phone_number"], "254700000901")
		self.assertTrue(frappe.db.exists("Payment Entry", row["payment_entry"]))

	def test_a_cheque_paid_later_shows_its_number_date_and_typed_remarks(self):
		invoice = create_sales_invoice(company=COMPANY, customer=CUSTOMER, rate=300, qty=1)
		pe = get_payment_entry("Sales Invoice", invoice.name)
		pe.mode_of_payment = "Cheque"
		pe.reference_no = "CHQ-7781"
		pe.reference_date = "2026-11-20"
		pe.custom_remarks = 1
		pe.remarks = "Post-dated, bank it on the 20th"
		pe.insert(ignore_permissions=True)
		pe.submit()

		rows = payment_details(frappe.get_doc("Sales Invoice", invoice.name))

		self.assertEqual(len(rows), 1, rows)
		self.assertEqual(rows[0]["payment_entry"], pe.name)
		self.assertEqual(rows[0]["reference_no"], "CHQ-7781")
		self.assertEqual(str(rows[0]["reference_date"]), "2026-11-20")
		self.assertEqual(rows[0]["remarks"], "Post-dated, bank it on the 20th")
		self.assertEqual(rows[0]["amount"], 300.0)


class TestPaymentDetailsEdges(MpesaFirstCase):
	def test_one_entry_split_over_two_reference_rows_counts_in_full(self):
		"""Payment terms write a reference row per term: a 300 cheque paid 150 + 150."""
		invoice = create_sales_invoice(company=COMPANY, customer=CUSTOMER, rate=300, qty=1)
		pe = get_payment_entry("Sales Invoice", invoice.name)
		pe.mode_of_payment = "Cheque"
		pe.reference_no, pe.reference_date = "CHQ-SPLIT", "2026-11-20"
		pe.references[0].allocated_amount = 150
		pe.paid_amount = pe.received_amount = 300
		pe.insert(ignore_permissions=True)
		pe.submit()
		# The second term's row, as payment terms write it (ERPNext tells the two apart by term).
		second = frappe.copy_doc(pe.references[0])
		second.update({"parent": pe.name, "parenttype": "Payment Entry", "parentfield": "references", "idx": 2})
		second.db_insert()

		rows = payment_details(frappe.get_doc("Sales Invoice", invoice.name))

		self.assertEqual([(r["payment_entry"], r["amount"]) for r in rows], [(pe.name, 300.0)])

	def test_a_cancelled_invoice_lists_no_payment_entries(self):
		"""ERPNext leaves the advance rows on a cancelled invoice; the entry may now pay another."""
		invoice = self._draft(rate=100, posting_date=frappe.utils.nowdate())
		invoice = self._record(invoice, self._receipt(100, "254700000902"))
		_allocate_receipts_before_submit(invoice)
		invoice.reload()
		invoice.submit()
		frappe.db.set_value("Sales Invoice", invoice.name, "docstatus", 2, update_modified=False)

		self.assertEqual([r for r in payment_details(frappe.get_doc("Sales Invoice", invoice.name)) if r["source"] == "payment_entry"], [])

	def test_a_refund_row_is_shown(self):
		invoice = frappe.get_doc("Sales Invoice", self._draft(rate=100, posting_date=frappe.utils.nowdate()).name)
		invoice.append("payments", {"mode_of_payment": "Cash", "amount": -100})
		invoice.db_update_all()
		rows = payment_details(frappe.get_doc("Sales Invoice", invoice.name))
		self.assertEqual([(r["mode_of_payment"], r["amount"]) for r in rows], [("Cash", -100.0)])

	def test_the_cash_row_shows_what_was_kept_and_says_what_was_tendered(self):
		invoice = self._draft(rate=800, posting_date=frappe.utils.nowdate())
		invoice.append("payments", {"mode_of_payment": "Cash", "amount": 1000})
		invoice.save(ignore_permissions=True)
		self.assertEqual(invoice.change_amount, 200)

		row = payment_details(invoice)[0]

		self.assertEqual((row["amount"], row["tendered"], row["change"]), (800.0, 1000.0, 200.0))

	def test_a_failing_payment_lookup_leaves_the_invoice_page_up(self):
		invoice = self._draft(rate=100, posting_date=frappe.utils.nowdate())
		invoice.append("payments", {"mode_of_payment": "Cash", "amount": 100})
		invoice.save(ignore_permissions=True)
		with patch("klik_pos.api.payment_rows.payment_details", side_effect=frappe.db.InternalError("boom")):
			res = get_invoice_details(invoice.name)
		self.assertTrue(res["success"], res)
		self.assertIsNone(res["data"]["payment_details"])

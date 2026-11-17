"""The invoice page lists each payment with what identifies it: the cheque or reference number
and date, the M-Pesa receipt and phone, the Payment Entry, and any remarks typed on it."""

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

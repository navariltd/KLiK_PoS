import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

from klik_pos.api.mpesa import process_mpesa


class TestProcessMpesa(FrappeTestCase):
	"""Regression coverage for the M-Pesa "Add Selected Payments" reconcile
	endpoint: the frontend called a whitelisted method that never existed
	(`frappe_mpsa_payments...mpesa_quick_pay.process_mpesa`), so clicking
	"Add Selected Payments" always 404'd. `klik_pos.api.mpesa.process_mpesa`
	replaces it, appending directly to the draft Sales Invoice's `payments`
	child table (klik_pos reconciles against a draft invoice, not a
	submitted Sales Order).

	Fixtures are real documents (not mocks) so `invoice.save()`/`.submit()`
	and the register row's own `before_submit`/`on_submit` are exercised
	end to end.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = "_Test Company"
		cls.customer = "_Test Customer"
		cls.other_customer = "_Test Customer 1"
		cls.shortcode = f"TSC{frappe.generate_hash(length=6).upper()}"
		cls.gateway_name = f"Test Mpesa Gateway {frappe.generate_hash(length=6)}"

		# Mpesa Settings.on_update() hard-commits real Payment Gateway/Account/
		# Mode of Payment records via an explicit frappe.db.commit() that this
		# test's rollback can't undo. db_insert() bypasses validate/on_update
		# entirely (raw INSERT), so the fixture stays transaction-safe.
		settings = frappe.get_doc(
			{
				"doctype": "Mpesa Settings",
				"payment_gateway_name": cls.gateway_name,
				"company": cls.company,
				"business_shortcode": cls.shortcode,
			}
		)
		settings.db_insert()

	def _draft_invoice(self, customer=None):
		# is_pos=1 matches the real POS checkout draft this endpoint targets:
		# ERPNext's calculate_paid_amount() (taxes_and_totals.py) wipes the
		# `payments` child table on every save for non-POS invoices, which
		# would silently discard whatever process_mpesa appends.
		return create_sales_invoice(
			company=self.company,
			customer=customer or self.customer,
			is_pos=1,
			do_not_save=True,
		)

	def _make_c2b_payment(self, amount=100, msisdn="254700000001"):
		# full_name is a derived/read-only field recomputed from
		# firstname/lastname in before_insert -> set_missing_values(); it
		# can't be set directly on insert.
		doc = frappe.get_doc(
			{
				"doctype": "Mpesa C2B Payment Register",
				"businessshortcode": self.shortcode,
				"transactiontype": "Pay Bill",
				"transid": f"TX{frappe.generate_hash(length=8).upper()}",
				"transtime": "120000",
				"transamount": amount,
				"billrefnumber": f"BILL{frappe.generate_hash(length=6).upper()}",
				"msisdn": msisdn,
				"firstname": "Zawadi",
				"lastname": "Mwangi",
				"posting_date": frappe.utils.nowdate(),
				"posting_time": frappe.utils.nowtime(),
				"company": self.company,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def test_single_row_no_merge(self):
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=150, msisdn="254711111111")

		result = process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=row.name,
			mode_of_payment="Cash",
			auto_save=1,
			auto_submit=0,
			merge_payments=0,
		)

		self.assertTrue(result["success"])
		self.assertEqual(result["total_amount"], 150)
		self.assertFalse(result["merged"])
		self.assertFalse(result["submitted"])
		self.assertEqual(len(result["payments_added"]), 1)
		self.assertEqual(result["payments_added"][0]["reference"], row.transid)

		invoice.reload()
		self.assertEqual(invoice.docstatus, 0)
		self.assertEqual(len(invoice.payments), 1)
		self.assertEqual(invoice.payments[0].mode_of_payment, "Cash")
		self.assertEqual(invoice.payments[0].amount, 150)
		self.assertEqual(invoice.payments[0].reference_no, row.transid)

		self.assertEqual(len(invoice.custom_mpesa_reconciled_payments), 1)
		child = invoice.custom_mpesa_reconciled_payments[0]
		self.assertEqual(child.mpesa_c2b_payment_register, row.name)
		self.assertEqual(child.transid, row.transid)
		self.assertEqual(child.amount, 150)
		self.assertEqual(child.msisdn, "254711111111")

		row.reload()
		self.assertEqual(row.docstatus, 1)
		self.assertEqual(row.customer, self.customer)
		self.assertEqual(row.mode_of_payment, "Cash")

	def test_multiple_rows_merged(self):
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row_a = self._make_c2b_payment(amount=100, msisdn="254722222222")
		row_b = self._make_c2b_payment(amount=200, msisdn="")

		result = process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=f"{row_a.name},{row_b.name}",
			mode_of_payment="Cash",
			auto_save=1,
			auto_submit=0,
			merge_payments=1,
		)

		self.assertTrue(result["success"])
		self.assertTrue(result["merged"])
		self.assertEqual(result["total_amount"], 300)
		self.assertEqual(len(result["payments_added"]), 1)
		expected_ref = f"{row_a.transid},{row_b.transid}"
		self.assertEqual(result["payments_added"][0]["reference"], expected_ref)

		invoice.reload()
		self.assertEqual(len(invoice.payments), 1)
		self.assertEqual(invoice.payments[0].amount, 300)
		self.assertEqual(invoice.payments[0].reference_no, expected_ref)
		# phone_number should be the first non-empty msisdn among selected rows.
		self.assertEqual(invoice.payments[0].phone_number, "254722222222")

		# One child row per consumed register row, even though merged into
		# a single invoice payment line.
		self.assertEqual(len(invoice.custom_mpesa_reconciled_payments), 2)
		recorded_names = {c.mpesa_c2b_payment_register for c in invoice.custom_mpesa_reconciled_payments}
		self.assertEqual(recorded_names, {row_a.name, row_b.name})

		row_a.reload()
		row_b.reload()
		self.assertEqual(row_a.docstatus, 1)
		self.assertEqual(row_b.docstatus, 1)

	def test_already_consumed_row_is_rejected(self):
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100)

		# Consume it once.
		process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=row.name,
			mode_of_payment="Cash",
			auto_save=1,
			auto_submit=0,
			merge_payments=0,
		)

		other_invoice = self._draft_invoice()
		other_invoice.insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			process_mpesa(
				doctype="Sales Invoice",
				invoice_name=other_invoice.name,
				customer=self.customer,
				mpesa_payments=row.name,
				mode_of_payment="Cash",
				auto_save=1,
				auto_submit=0,
				merge_payments=0,
			)

		other_invoice.reload()
		self.assertEqual(len(other_invoice.payments), 0)
		self.assertEqual(len(other_invoice.custom_mpesa_reconciled_payments), 0)

	def test_customer_mismatch_is_rejected(self):
		invoice = self._draft_invoice(customer=self.customer)
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100)

		with self.assertRaises(frappe.ValidationError):
			process_mpesa(
				doctype="Sales Invoice",
				invoice_name=invoice.name,
				customer=self.other_customer,
				mpesa_payments=row.name,
				mode_of_payment="Cash",
				auto_save=1,
				auto_submit=0,
				merge_payments=0,
			)

		invoice.reload()
		self.assertEqual(len(invoice.payments), 0)
		row.reload()
		self.assertEqual(row.docstatus, 0)

	def test_auto_submit_zero_keeps_invoice_draft(self):
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100)

		process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=row.name,
			mode_of_payment="Cash",
			auto_save=1,
			auto_submit=0,
			merge_payments=0,
		)

		invoice.reload()
		self.assertEqual(invoice.docstatus, 0)
		self.assertEqual(len(invoice.payments), 1)

	def test_auto_submit_one_submits_invoice(self):
		# Posting date pinned to a Fiscal Year that isn't restricted to a
		# specific company (unlike this site's "2026" Fiscal Year, which is
		# scoped only to "Dev Co"/"Temp Co" and would reject "_Test Company"
		# for today's date) so submit's fiscal-year check passes regardless
		# of which companies the site's real Fiscal Year records are scoped to.
		invoice = self._draft_invoice()
		invoice.posting_date = "2029-06-15"
		invoice.set_posting_time = 1
		invoice.posting_time = "10:00:00"
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100)

		result = process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=row.name,
			mode_of_payment="Cash",
			auto_save=1,
			auto_submit=1,
			merge_payments=0,
		)

		self.assertTrue(result["submitted"])
		invoice.reload()
		self.assertEqual(invoice.docstatus, 1)

	def test_duplicate_register_row_name_in_same_call_is_rejected(self):
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100)

		with self.assertRaises(frappe.ValidationError):
			process_mpesa(
				doctype="Sales Invoice",
				invoice_name=invoice.name,
				customer=self.customer,
				mpesa_payments=f"{row.name},{row.name}",
				mode_of_payment="Cash",
				auto_save=1,
				auto_submit=0,
				merge_payments=0,
			)

		invoice.reload()
		self.assertEqual(len(invoice.payments), 0)
		row.reload()
		self.assertEqual(row.docstatus, 0)

	def test_auto_save_zero_is_rejected(self):
		# auto_save=0 is not implemented (process_mpesa always saves the
		# invoice); a caller passing 0 expecting some no-op/preview mode must
		# get a clear error instead of a silent real save.
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100)

		with self.assertRaises(frappe.ValidationError):
			process_mpesa(
				doctype="Sales Invoice",
				invoice_name=invoice.name,
				customer=self.customer,
				mpesa_payments=row.name,
				mode_of_payment="Cash",
				auto_save=0,
				auto_submit=0,
				merge_payments=0,
			)

		invoice.reload()
		self.assertEqual(len(invoice.payments), 0)
		row.reload()
		self.assertEqual(row.docstatus, 0)

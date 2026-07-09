import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from erpnext.accounts.doctype.pos_profile.test_pos_profile import make_pos_profile
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

from klik_pos.api.mpesa import process_mpesa
from klik_pos.api.sales_invoice import submit_draft_invoice


class TestProcessMpesa(FrappeTestCase):
	"""Regression coverage for M-Pesa reconciliation in klik_pos's POS checkout.

	`process_mpesa` backs the "Add Selected Payments" button in the POS
	checkout's "M-Pesa Payment Options" modal. It used to append mpesa
	amounts directly into the draft Sales Invoice's own `payments` child
	table and submit the `Mpesa C2B Payment Register` row without setting
	`submit_payment`, which meant no `Payment Entry` was ever created -- the
	M-Pesa receipt was invisible to Payment Reconciliation, unallocated
	advances, and bank-reconciliation tooling, and any overpayment was
	stranded inside the one invoice's ledger with no way to reuse it on a
	future purchase.

	It now only records which register rows were selected
	(`custom_mpesa_reconciled_payments`, deferred/unconsumed). At finalization
	the hybrid flow embeds the paid portion (capped at the invoice's payable
	total) as `Sales Invoice Payment` rows BEFORE submit -- so POS paid-amount
	validation passes and the take is visible to shift reconciliation -- via
	`_embed_mpesa_payments`, then after submit consumes the register rows
	(without minting per-row Payment Entries) and turns any overpaid excess into
	an unallocated customer-credit Payment Entry via
	`_finalize_mpesa_reconciliation`. Both run inline for `auto_submit=1`, or
	from `submit_draft_invoice` for the normal POS checkout path where
	submission happens in a later request.

	Fixtures are real documents (not mocks) so `invoice.save()`/`.submit()`,
	the register row's own `before_submit`/`on_submit`, and Payment
	Reconciliation are exercised end to end.
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

	def _draft_invoice(self, customer=None, rate=100):
		# is_pos=1 matches the real POS checkout draft this endpoint targets.
		# Posting date is pinned to a Fiscal Year that isn't restricted to a
		# specific company (unlike this site's "2026" Fiscal Year, which is
		# scoped only to "Dev Co"/"Temp Co" and would reject "_Test Company"
		# for today's date), so `.submit()` passes regardless of which
		# companies the site's real Fiscal Year records are scoped to.
		invoice = create_sales_invoice(
			company=self.company,
			customer=customer or self.customer,
			is_pos=1,
			rate=rate,
			posting_date="2029-06-15",
			do_not_save=True,
		)
		invoice.set_posting_time = 1
		invoice.posting_time = "10:00:00"
		return invoice

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
		# set_missing_values() hardcodes currency="KES", but _Test Company's
		# receivable account is INR-denominated, and create_payment_entry()
		# throws on a party/transaction currency mismatch. Real companies
		# configured for Mpesa are always KES-denominated -- this override is
		# a _Test Company fixture artifact only, not a production concern.
		frappe.db.set_value("Mpesa C2B Payment Register", doc.name, "currency", "INR")
		doc.reload()
		return doc

	def _ensure_bank_mode_of_payment(self, mode_of_payment="Cheque", account="_Test Bank - _TC"):
		# Real M-Pesa modes of payment resolve to a Bank-type account (unlike
		# this file's other fixtures, which all use "Cash" and so never
		# exercise Payment Entry's Bank-transaction validation). "Cheque" is a
		# stock Mode of Payment already typed "Bank"; give it a default
		# account for _Test Company if one isn't already configured.
		if not frappe.db.get_value(
			"Mode of Payment Account", {"company": self.company, "parent": mode_of_payment}
		):
			mop = frappe.get_doc("Mode of Payment", mode_of_payment)
			mop.append("accounts", {"company": self.company, "default_account": account})
			mop.save()
		return mode_of_payment

	def test_records_traceability_without_touching_payments(self):
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100, msisdn="254711111111")

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
		self.assertEqual(result["total_amount"], 100)
		self.assertFalse(result["merged"])
		self.assertFalse(result["submitted"])
		self.assertNotIn("mpesa_reconciliation", result)
		self.assertEqual(len(result["payments_added"]), 1)
		self.assertEqual(result["payments_added"][0]["reference"], row.transid)

		invoice.reload()
		self.assertEqual(invoice.docstatus, 0)
		# process_mpesa no longer touches the invoice's own payments table.
		self.assertEqual(len(invoice.payments), 0)

		self.assertEqual(len(invoice.custom_mpesa_reconciled_payments), 1)
		child = invoice.custom_mpesa_reconciled_payments[0]
		self.assertEqual(child.mpesa_c2b_payment_register, row.name)
		self.assertEqual(child.transid, row.transid)
		self.assertEqual(child.amount, 100)
		self.assertEqual(child.msisdn, "254711111111")
		self.assertEqual(child.mode_of_payment, "Cash")

		# The register row is left untouched -- consumption is deferred
		# until the invoice is actually submitted.
		row.reload()
		self.assertEqual(row.docstatus, 0)
		self.assertFalse(row.payment_entry)

	def test_multiple_rows_recorded_without_merge_effect(self):
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row_a = self._make_c2b_payment(amount=40, msisdn="254722222222")
		row_b = self._make_c2b_payment(amount=60, msisdn="")

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
		self.assertEqual(result["total_amount"], 100)
		# merge_payments only affects the display summary now, since there's
		# no embedded payment row to actually merge.
		self.assertEqual(len(result["payments_added"]), 1)
		expected_ref = f"{row_a.transid},{row_b.transid}"
		self.assertEqual(result["payments_added"][0]["reference"], expected_ref)

		invoice.reload()
		self.assertEqual(len(invoice.payments), 0)

		# One traceability row per selected register row regardless of the
		# merge flag.
		self.assertEqual(len(invoice.custom_mpesa_reconciled_payments), 2)
		recorded_names = {c.mpesa_c2b_payment_register for c in invoice.custom_mpesa_reconciled_payments}
		self.assertEqual(recorded_names, {row_a.name, row_b.name})

		row_a.reload()
		row_b.reload()
		self.assertEqual(row_a.docstatus, 0)
		self.assertEqual(row_b.docstatus, 0)

	def test_already_consumed_row_is_rejected(self):
		invoice = self._draft_invoice()
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100)

		# Select + finalize it once (auto_submit=1 submits the invoice and
		# immediately consumes the register row).
		process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=row.name,
			mode_of_payment="Cash",
			auto_save=1,
			auto_submit=1,
			merge_payments=0,
		)
		row.reload()
		self.assertEqual(row.docstatus, 1)

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

	def test_auto_submit_zero_keeps_invoice_and_register_unconsumed(self):
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
		self.assertEqual(len(invoice.payments), 0)

		row.reload()
		self.assertEqual(row.docstatus, 0)
		self.assertFalse(row.payment_entry)

	def test_auto_submit_one_finalizes_reconciliation(self):
		"""Exact payment (invoice 100, one 100 receipt): the receipt is embedded
		on the invoice as a Sales Invoice Payment row (no excess), the invoice
		submits fully paid, and the register row is consumed WITHOUT minting a
		per-row Payment Entry (submit_payment=0)."""
		invoice = self._draft_invoice(rate=100)
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
		# Exact pay -> no overpaid excess -> no credit Payment Entry.
		self.assertEqual(result["mpesa_reconciliation"], [])

		invoice.reload()
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(flt(invoice.outstanding_amount), 0)
		# The receipt is embedded on the invoice, not held as a PE.
		self.assertEqual(len(invoice.payments), 1)
		self.assertEqual(flt(invoice.payments[0].amount), 100)
		self.assertEqual(flt(invoice.paid_amount), 100)

		row.reload()
		self.assertEqual(row.docstatus, 1)
		# submit_payment=0 -> the register hook mints no per-row Payment Entry.
		self.assertFalse(row.payment_entry)

	def test_embed_survives_real_pos_profile_with_configured_payment_methods(self):
		"""Regression: production invoices carry a real `pos_profile` with
		configured `POS Payment Method` rows, unlike the other fixtures in this
		file which leave `pos_profile` unset. `_embed_mpesa_payments` used to
		call `invoice.set_missing_values()` AFTER appending the embedded M-Pesa
		payment row; with a real pos_profile attached, that reaches ERPNext's
		`set_pos_fields` -> `update_multi_mode_option`, which unconditionally
		wipes and rebuilds the `payments` child table from the POS Profile's
		configured modes (with no amount set), erasing the just-embedded row.
		The invoice would submit with paid_amount=0 and the full grand_total
		left outstanding, even though `custom_mpesa_reconciled_payments` shows
		the receipt was reconciled.
		"""
		pos_profile = make_pos_profile(company=self.company)

		invoice = self._draft_invoice(rate=100)
		invoice.pos_profile = pos_profile.name
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
		self.assertEqual(len(invoice.payments), 1)
		self.assertEqual(flt(invoice.payments[0].amount), 100)
		self.assertEqual(flt(invoice.paid_amount), 100)
		self.assertEqual(flt(invoice.outstanding_amount), 0)

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

	def test_submit_draft_invoice_finalizes_deferred_mpesa_reconciliation(self):
		"""End-to-end regression for the real POS checkout path: select M-Pesa
		payments on a draft (auto_submit=0), then finalize via
		submit_draft_invoice (as PaymentDialog.tsx's c2b flow does, with
		data=None). Embedding the paid portion + register consumption must happen
		here, not in process_mpesa.
		"""
		invoice = self._draft_invoice(rate=100)
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
		row.reload()
		self.assertEqual(row.docstatus, 0)

		result = submit_draft_invoice(invoice.name, data=None)

		self.assertTrue(result["success"])
		# Exact pay -> no excess credit PE, so the key is omitted.
		self.assertNotIn("mpesa_reconciliation", result)

		invoice.reload()
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(flt(invoice.outstanding_amount), 0)
		self.assertEqual(len(invoice.payments), 1)
		self.assertEqual(flt(invoice.paid_amount), 100)

		row.reload()
		self.assertEqual(row.docstatus, 1)
		self.assertFalse(row.payment_entry)

	def test_overpayment_creates_unallocated_customer_credit(self):
		"""Regression for the reported bug: invoice total 100, M-Pesa payments
		totalling 130 (60 + 70). The paid portion (100) is embedded on the
		invoice so it submits fully paid and reconciles against the shift; the
		extra 30 becomes a real, reusable unallocated Payment Entry credit --
		not cash "change" and not a negative outstanding_amount.
		"""
		invoice = self._draft_invoice(rate=100)
		invoice.insert(ignore_permissions=True)
		row_a = self._make_c2b_payment(amount=60, msisdn="254733333333")
		row_b = self._make_c2b_payment(amount=70, msisdn="254744444444")

		process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=f"{row_a.name},{row_b.name}",
			mode_of_payment="Cash",
			auto_save=1,
			auto_submit=0,
			merge_payments=0,
		)

		result = submit_draft_invoice(invoice.name, data=None)
		self.assertTrue(result["success"])

		invoice.reload()
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(flt(invoice.outstanding_amount), 0)
		# Paid portion embedded (capped at the 100 total) -> no cash change.
		self.assertEqual(flt(invoice.paid_amount), 100)
		self.assertEqual(flt(invoice.change_amount or 0), 0)
		self.assertEqual(sum(flt(p.amount) for p in invoice.payments), 100)

		row_a.reload()
		row_b.reload()
		self.assertEqual(row_a.docstatus, 1)
		self.assertEqual(row_b.docstatus, 1)
		# No per-row Payment Entry -- the paid portion lives on the invoice.
		self.assertFalse(row_a.payment_entry)
		self.assertFalse(row_b.payment_entry)

		# Exactly one excess credit PE for the overpaid 30, fully unallocated.
		self.assertEqual(len(result["mpesa_reconciliation"]), 1)
		recon = result["mpesa_reconciliation"][0]
		self.assertEqual(flt(recon["excess_amount"]), 30)
		pe = frappe.get_doc("Payment Entry", recon["payment_entry"])
		self.assertEqual(pe.docstatus, 1)
		self.assertEqual(pe.payment_type, "Receive")
		self.assertEqual(pe.party, self.customer)
		self.assertEqual(flt(pe.unallocated_amount), 30)
		self.assertEqual(len(pe.references), 0)

		# The overflowing traceability row is linked to the credit PE.
		invoice.reload()
		linked = [
			c.excess_payment_entry
			for c in invoice.custom_mpesa_reconciled_payments
			if c.excess_payment_entry
		]
		self.assertIn(pe.name, linked)

	def test_overpayment_credit_sets_reference_for_bank_mode(self):
		"""Regression: real M-Pesa modes of payment resolve to a Bank-type
		account (unlike "Cash", used by the other tests here), and ERPNext's
		Payment Entry.validate_transaction_reference() throws "Reference No
		and Reference Date is mandatory for Bank transaction" if either is
		blank. create_payment_entry() used to be called for the excess credit
		without a reference_date at all, and with a generic description (not
		the real M-Pesa Trans ID) as reference_no -- invisible to this file's
		other tests because they all use "Cash", which never reaches that
		validation.
		"""
		mode_of_payment = self._ensure_bank_mode_of_payment()

		invoice = self._draft_invoice(rate=100)
		invoice.insert(ignore_permissions=True)
		row_a = self._make_c2b_payment(amount=60, msisdn="254755555555")
		row_b = self._make_c2b_payment(amount=70, msisdn="254766666666")

		process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=f"{row_a.name},{row_b.name}",
			mode_of_payment=mode_of_payment,
			auto_save=1,
			auto_submit=0,
			merge_payments=0,
		)

		# Used to throw here: "Reference No and Reference Date is mandatory
		# for Bank transaction" while creating the excess credit Payment Entry.
		result = submit_draft_invoice(invoice.name, data=None)
		self.assertTrue(result["success"])

		recon = result["mpesa_reconciliation"][0]
		pe = frappe.get_doc("Payment Entry", recon["payment_entry"])
		self.assertEqual(pe.docstatus, 1)
		self.assertTrue(pe.reference_date)
		self.assertEqual(frappe.utils.getdate(pe.reference_date), frappe.utils.getdate(invoice.posting_date))
		# reference_no must trace back to the real M-Pesa receipt that
		# straddled the cap (row_a's 60 is fully embedded with no excess;
		# row_b's 70 embeds 40 and overflows the remaining 30), not a
		# generic description.
		self.assertIn(row_b.transid, pe.reference_no)
		self.assertNotIn(row_a.transid, pe.reference_no)

	def test_embedded_mpesa_is_visible_to_shift_reconciliation(self):
		"""The paid portion must appear in the POS closing/drawer reconciliation,
		which aggregates `Sales Invoice Payment` by mode (never Payment Entries).
		"""
		invoice = self._draft_invoice(rate=100)
		invoice.insert(ignore_permissions=True)
		row = self._make_c2b_payment(amount=100)

		process_mpesa(
			doctype="Sales Invoice",
			invoice_name=invoice.name,
			customer=self.customer,
			mpesa_payments=row.name,
			mode_of_payment="Cash",
			auto_save=1,
			auto_submit=1,
			merge_payments=0,
		)

		# Mirror pos_entry._calculate_payment_reconciliation's aggregation.
		rows = frappe.db.sql(
			"""
			SELECT sip.mode_of_payment, SUM(sip.amount) AS total
			FROM `tabSales Invoice` si
			JOIN `tabSales Invoice Payment` sip ON si.name = sip.parent
			WHERE si.name = %s AND si.docstatus = 1
			GROUP BY sip.mode_of_payment
			""",
			(invoice.name,),
			as_dict=True,
		)
		by_mode = {r.mode_of_payment: flt(r.total) for r in rows}
		self.assertEqual(by_mode.get("Cash"), 100)

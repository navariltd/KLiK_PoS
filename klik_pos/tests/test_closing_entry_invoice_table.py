"""The closing entry's invoice breakdown must actually have somewhere to go.

_populate_sales_invoices_to_closing_entry appends to `custom_sales_invoice`, but nothing ever
created that field: klik_pos ships only Property Setter fixtures, and the Klik Sales Invoice
Reference child doctype it targets was added without the field to hold it. Every shift close
raised AttributeError, the surrounding try/except swallowed it so the closing entry still
saved, and the breakdown silently never appeared - on any site.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.setup.pos_profile_fields import install_pos_closing_entry_invoice_table

FIELDNAME = "custom_sales_invoice"


class TestClosingEntryInvoiceTable(FrappeTestCase):
	def test_the_field_exists_after_install(self):
		install_pos_closing_entry_invoice_table()

		field = frappe.get_meta("POS Closing Entry").get_field(FIELDNAME)
		self.assertIsNotNone(field, "the child table the code appends to was never created")
		self.assertEqual(field.fieldtype, "Table")
		self.assertEqual(field.options, "Klik Sales Invoice Reference")

	def test_it_is_read_only(self):
		"""Rebuilt from the invoices themselves; not something to hand-edit."""
		install_pos_closing_entry_invoice_table()

		self.assertTrue(frappe.get_meta("POS Closing Entry").get_field(FIELDNAME).read_only)

	def test_installing_twice_does_not_duplicate(self):
		install_pos_closing_entry_invoice_table()
		install_pos_closing_entry_invoice_table()

		self.assertEqual(
			frappe.db.count("Custom Field", {"dt": "POS Closing Entry", "fieldname": FIELDNAME}), 1
		)

	def test_a_closing_entry_can_hold_invoice_rows(self):
		"""The regression: this append is what raised AttributeError on every shift close."""
		install_pos_closing_entry_invoice_table()
		frappe.clear_cache(doctype="POS Closing Entry")

		doc = frappe.new_doc("POS Closing Entry")
		doc.append(
			FIELDNAME,
			{
				"sales_invoice": None,
				"customer": None,
				"posting_date": frappe.utils.nowdate(),
				"amount": 1500,
			},
		)

		self.assertEqual(len(doc.get(FIELDNAME)), 1)
		self.assertEqual(doc.get(FIELDNAME)[0].amount, 1500)

	def test_the_child_doctype_carries_the_fields_the_code_writes(self):
		written = {"sales_invoice", "customer", "posting_date", "amount"}
		available = {f.fieldname for f in frappe.get_meta("Klik Sales Invoice Reference").fields}

		self.assertTrue(written.issubset(available), f"child table is missing {sorted(written - available)}")

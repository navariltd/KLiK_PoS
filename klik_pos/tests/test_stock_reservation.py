"""Stock reservation must honour the site switch, and must be releasable by whoever took it.

klik_pos builds Stock Reservation Entries by hand against a Sales Invoice - a voucher type
erpnext itself refuses (validate_stock_reservation_settings allows Sales Order only) - and
submits them with ignore_permissions. Two consequences were live in production:

1. The entries were created whether or not Stock Settings enabled reservation at all, so a
   site that had deliberately switched it off still accumulated held stock.
2. Creating a reservation bypassed permissions but cancelling one did not, so any role
   without Stock Reservation Entry rights (every ERPNext Express role) could take stock and
   never give it back. The failure landed in before_submit, aborting the submit and
   stranding the invoice in the queue with its stock still held.
"""

from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_invoice import (
	_cancel_sales_invoice_reservations,
	_reserve_stock_for_queued_invoice,
	_stock_reservation_enabled,
)

ITEM_CODE = "TEST-SRE-ITEM-001"
ITEM_GROUP = "TEST-SRE-GROUP"
NO_RIGHTS_USER = "sre_no_rights@example.com"


def _active_entries(voucher_no):
	return frappe.get_all(
		"Stock Reservation Entry",
		filters={"voucher_no": voucher_no, "voucher_type": "Sales Invoice", "docstatus": 1},
		pluck="name",
	)


class StockReservationTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Borrow the company and customer from an invoice the site has already posted, rather
		# than picking the first row of each table. A Sales Invoice validates its receivable
		# account currency against the company's, and dev sites routinely carry companies where
		# those disagree - a known-good pairing keeps this suite testing reservation logic
		# instead of somebody's chart of accounts.
		reference = frappe.get_all(
			"Sales Invoice",
			filters={"docstatus": 1, "is_return": 0},
			fields=["company", "customer"],
			limit=1,
		)
		if not reference:
			raise cls.skipException("no posted Sales Invoice on this site to borrow a company from")
		cls.company = reference[0].company
		cls.customer = reference[0].customer
		cls.warehouse = frappe.db.get_value("Warehouse", {"is_group": 0, "company": cls.company}, "name")

		if not frappe.db.exists("Item Group", ITEM_GROUP):
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": ITEM_GROUP,
					"parent_item_group": "All Item Groups",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Item", ITEM_CODE):
			item = frappe.new_doc("Item")
			item.item_code = ITEM_CODE
			item.item_name = ITEM_CODE
			item.item_group = ITEM_GROUP
			item.stock_uom = "Nos"
			item.is_stock_item = 1
			item.is_sales_item = 1
			item.insert(ignore_permissions=True)

		# Real stock via a Material Receipt, not a hand-written Bin row: Stock Reservation
		# Entry computes availability from the stock ledger, so a Bin with no ledger behind it
		# reads as zero and every reservation is refused.
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		cls.stock_entry = make_stock_entry(
			item_code=ITEM_CODE,
			target=cls.warehouse,
			qty=100,
			basic_rate=10,
			company=cls.company,
		)

		# Created once and left in place. Deleting a User cascades into Contact/ToDo and
		# deadlocks when two test classes tear down in sequence; the fixture is inert
		# (no roles, so no rights to anything) so leaving it costs nothing.
		if not frappe.db.exists("User", NO_RIGHTS_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": NO_RIGHTS_USER,
					"first_name": "No SRE Rights",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for name in frappe.get_all("Stock Reservation Entry", filters={"item_code": ITEM_CODE}, pluck="name"):
			entry = frappe.get_doc("Stock Reservation Entry", name)
			entry.flags.ignore_permissions = True
			if entry.docstatus == 1:
				entry.cancel()
			frappe.delete_doc("Stock Reservation Entry", name, force=True, ignore_permissions=True)
		if getattr(cls, "stock_entry", None) and frappe.db.exists("Stock Entry", cls.stock_entry.name):
			entry = frappe.get_doc("Stock Entry", cls.stock_entry.name)
			if entry.docstatus == 1:
				entry.flags.ignore_permissions = True
				entry.cancel()
			frappe.delete_doc("Stock Entry", cls.stock_entry.name, force=True, ignore_permissions=True)
		for name in frappe.get_all("Bin", filters={"item_code": ITEM_CODE}, pluck="name"):
			frappe.delete_doc("Bin", name, force=True, ignore_permissions=True)
		if frappe.db.exists("Item", ITEM_CODE):
			frappe.delete_doc("Item", ITEM_CODE, force=True, ignore_permissions=True)
		if frappe.db.exists("Item Group", ITEM_GROUP):
			frappe.delete_doc("Item Group", ITEM_GROUP, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def _draft_invoice(self, reserve_stock=1):
		doc = frappe.new_doc("Sales Invoice")
		doc.customer = self.customer
		doc.company = self.company
		doc.reserve_stock = reserve_stock
		doc.append(
			"items",
			{"item_code": ITEM_CODE, "qty": 2, "rate": 10, "warehouse": self.warehouse},
		)
		doc.insert(ignore_permissions=True)
		self.addCleanup(self._purge_invoice, doc.name)
		return doc

	def _purge_invoice(self, name):
		self._purge_entries(name)
		if frappe.db.exists("Sales Invoice", name):
			frappe.delete_doc("Sales Invoice", name, force=True, ignore_permissions=True)

	@staticmethod
	def _purge_entries(voucher_no):
		for name in frappe.get_all(
			"Stock Reservation Entry", filters={"voucher_no": voucher_no}, pluck="name"
		):
			entry = frappe.get_doc("Stock Reservation Entry", name)
			entry.flags.ignore_permissions = True
			if entry.docstatus == 1:
				entry.cancel()
			frappe.delete_doc("Stock Reservation Entry", name, force=True, ignore_permissions=True)

	def _set_reservation_enabled(self, enabled):
		frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1 if enabled else 0)
		frappe.clear_cache()
		self.addCleanup(frappe.clear_cache)


class TestReservationHonoursTheSiteSwitch(StockReservationTestCase):
	def test_no_entries_created_when_reservation_is_disabled(self):
		self._set_reservation_enabled(False)
		doc = self._draft_invoice()

		_reserve_stock_for_queued_invoice(doc)

		self.assertFalse(_stock_reservation_enabled())
		self.assertEqual(_active_entries(doc.name), [], "reserved stock on a site that forbids it")

	def test_entries_created_when_reservation_is_enabled(self):
		self._set_reservation_enabled(True)
		doc = self._draft_invoice()

		_reserve_stock_for_queued_invoice(doc)

		self.assertTrue(_active_entries(doc.name), "reservation is enabled but nothing was reserved")

	def test_an_invoice_flagged_before_the_switch_flipped_can_still_release(self):
		"""Turning the switch off must not strand reservations that already exist."""
		self._set_reservation_enabled(True)
		doc = self._draft_invoice()
		_reserve_stock_for_queued_invoice(doc)
		self.assertTrue(_active_entries(doc.name))

		self._set_reservation_enabled(False)
		_cancel_sales_invoice_reservations(doc.name)

		self.assertEqual(_active_entries(doc.name), [], "reservations orphaned by the switch")


class TestReservationCanBeReleasedWithoutRights(StockReservationTestCase):
	def setUp(self):
		self._set_reservation_enabled(True)

	def test_cancel_succeeds_for_a_user_without_stock_reservation_entry_rights(self):
		doc = self._draft_invoice()
		_reserve_stock_for_queued_invoice(doc)
		self.assertTrue(_active_entries(doc.name))

		original = frappe.session.user
		try:
			frappe.set_user(NO_RIGHTS_USER)
			self.assertFalse(
				frappe.has_permission("Stock Reservation Entry", "write"),
				"fixture user unexpectedly has rights - the test would pass vacuously",
			)
			_cancel_sales_invoice_reservations(doc.name)
		finally:
			frappe.set_user(original)

		self.assertEqual(_active_entries(doc.name), [], "reservation could be taken but not released")

	def test_erpnext_helper_would_still_refuse_that_user(self):
		"""Pins why we cannot just call erpnext's helper: its bare cancel() checks rights."""
		from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
			cancel_stock_reservation_entries,
		)

		doc = self._draft_invoice()
		_reserve_stock_for_queued_invoice(doc)
		self.assertTrue(_active_entries(doc.name))

		original = frappe.session.user
		try:
			frappe.set_user(NO_RIGHTS_USER)
			with self.assertRaises(frappe.PermissionError):
				cancel_stock_reservation_entries(
					voucher_type="Sales Invoice", voucher_no=doc.name, notify=False
				)
		finally:
			frappe.set_user(original)

	def test_cancelling_nothing_is_a_no_op(self):
		_cancel_sales_invoice_reservations("SINV-DOES-NOT-EXIST")

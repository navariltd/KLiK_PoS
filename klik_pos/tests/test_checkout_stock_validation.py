"""Overselling must be caught before the customer pays, not after.

_validate_reserved_stock_for_items used to bail unless the invoice was flagged reserve_stock.
That made the checkout preview's call dead code - build_sales_invoice_doc never sets the flag -
and, once reservation became conditional on the Stock Settings switch, disabled the queue
path's check too on any site with reservation turned off. Both cases pushed an oversell past
checkout to fail at submit, after the money had been taken.
"""

from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_invoice import _validate_reserved_stock_for_items

ITEM_GROUP = "TEST-CHECKOUT-STOCK-GROUP"
ITEM_CODE = "TEST-CHECKOUT-STOCK-ITEM"


class TestCheckoutStockValidation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse, cls.company = frappe.db.get_value(
			"Warehouse", {"is_group": 0, "company": ["is", "set"]}, ["name", "company"]
		)

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

		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		cls.stock_entry = make_stock_entry(
			item_code=ITEM_CODE, target=cls.warehouse, qty=5, basic_rate=10, company=cls.company
		)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
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

	def _doc(self, qty, reserve_stock=None):
		"""A checkout preview. reserve_stock=None mirrors build_sales_invoice_doc, which
		never sets the flag - the case that made this check dead."""
		doc = SimpleNamespace(
			items=[SimpleNamespace(item_code=ITEM_CODE, warehouse=self.warehouse, qty=qty, stock_qty=qty)]
		)
		if reserve_stock is not None:
			doc.reserve_stock = reserve_stock
		return doc

	def test_an_oversell_is_blocked_even_though_the_preview_has_no_reserve_flag(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			_validate_reserved_stock_for_items(self._doc(qty=50))

		self.assertIn("Insufficient stock", str(caught.exception))

	def test_an_oversell_is_blocked_when_the_site_does_not_reserve(self):
		with self.assertRaises(frappe.ValidationError):
			_validate_reserved_stock_for_items(self._doc(qty=50, reserve_stock=0))

	def test_a_sale_within_stock_passes(self):
		_validate_reserved_stock_for_items(self._doc(qty=2))

	def test_selling_exactly_what_is_on_hand_passes(self):
		_validate_reserved_stock_for_items(self._doc(qty=5))

	def test_an_empty_cart_is_not_an_error(self):
		_validate_reserved_stock_for_items(SimpleNamespace(items=[]))

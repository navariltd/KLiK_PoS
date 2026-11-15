"""A disabled Price List is off the POS: its Item Prices are not offered as a price to switch
to (the cart line's Quick Switch Price menu), nor picked up as a fallback price."""

import frappe
from erpnext.stock.doctype.item.test_item import make_item
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.item.item_details import get_full_pricing_and_batch_details
from klik_pos.testing import any_till_currency
from klik_pos.api.item.item_price import fetch_item_price, get_price_list_with_customer_priority

ITEM = "_Test KLiK Disabled PL Item"
ENABLED = "_Test KLiK PL Enabled"
DISABLED = "_Test KLiK PL Disabled"
EMPTY = "_Test KLiK PL Empty"
CUSTOMER = "_Test KLiK PL Customer"
GROUP = "_Test KLiK PL Group"


class TestDisabledPriceLists(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		any_till_currency(cls)
		frappe.set_user("Administrator")
		if not frappe.db.exists("UOM", "Box"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "Box"}).insert()
		item = make_item(ITEM, {"is_stock_item": 1, "stock_uom": "Nos"})
		if not any(row.uom == "Box" for row in item.uoms):
			item.append("uoms", {"uom": "Box", "conversion_factor": 12})
			item.save()
		for name in (ENABLED, DISABLED, EMPTY):
			if not frappe.db.exists("Price List", name):
				frappe.get_doc(
					{"doctype": "Price List", "price_list_name": name, "selling": 1, "currency": "INR"}
				).insert()
			frappe.db.set_value("Price List", name, "enabled", 1)
		for price_list, rate in ((ENABLED, 100), (DISABLED, 70)):
			if not frappe.db.exists("Item Price", {"item_code": ITEM, "price_list": price_list}):
				frappe.get_doc(
					{
						"doctype": "Item Price",
						"item_code": ITEM,
						"price_list": price_list,
						"uom": "Nos",
						"price_list_rate": rate,
					}
				).insert()
		if not frappe.db.exists("Customer Group", GROUP):
			frappe.get_doc(
				{"doctype": "Customer Group", "customer_group_name": GROUP, "parent_customer_group": "All Customer Groups"}
			).insert()
		if not frappe.db.exists("Customer", CUSTOMER):
			frappe.get_doc({"doctype": "Customer", "customer_name": CUSTOMER, "customer_group": GROUP}).insert()
		# ERPNext refuses an Item Price on a disabled list, so the price goes in first.
		frappe.db.set_value("Price List", DISABLED, "enabled", 0)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.delete("Item Price", {"item_code": ITEM})
		frappe.delete_doc("Customer", CUSTOMER, force=True, ignore_missing=True)
		frappe.delete_doc("Customer Group", GROUP, force=True, ignore_missing=True)
		for name in (ENABLED, DISABLED, EMPTY):
			frappe.delete_doc("Price List", name, force=True, ignore_missing=True)
		frappe.delete_doc("Item", ITEM, force=True, ignore_missing=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_the_switch_menu_leaves_out_a_disabled_price_list(self):
		offered = {p["price_list"] for p in get_full_pricing_and_batch_details(ITEM)["price_lists"]}
		self.assertIn(ENABLED, offered)
		self.assertNotIn(DISABLED, offered)

	def test_the_fallback_price_skips_a_disabled_price_list(self):
		"""The till's list has no price for the item; the fallback (another list's price in the
		stock UOM, times the Box factor) must come from an enabled list."""
		frappe.db.set_value("Item Price", {"item_code": ITEM, "price_list": DISABLED}, "modified", "2099-01-01")
		price = fetch_item_price(ITEM, price_list=EMPTY, uom="Box")
		self.assertEqual(price.get("price"), 1200)

	def _pickers(self):
		"""The one resolver every place that prices from a customer's list goes through, with the
		till patched in and passed in."""
		from unittest.mock import patch

		till = frappe._dict(selling_price_list=ENABLED)
		with patch("klik_pos.api.item.item_price.get_current_pos_profile", return_value=till):
			yield "customer priority", get_price_list_with_customer_priority(CUSTOMER)
		yield "till passed in", get_price_list_with_customer_priority(CUSTOMER, pos_profile=till)

	def _set_defaults(self, customer_list, group_list):
		frappe.db.set_value("Customer", CUSTOMER, "default_price_list", customer_list)
		frappe.db.set_value("Customer Group", GROUP, "default_price_list", group_list)

	def test_a_customer_default_on_a_disabled_list_falls_back_to_the_till(self):
		self._set_defaults(DISABLED, None)
		for picker, price_list in self._pickers():
			self.assertEqual(price_list, ENABLED, picker)

	def test_a_group_default_on_a_disabled_list_falls_back_to_the_till(self):
		self._set_defaults(None, DISABLED)
		for picker, price_list in self._pickers():
			self.assertEqual(price_list, ENABLED, picker)

	def test_an_enabled_customer_default_still_wins(self):
		self._set_defaults(EMPTY, None)
		for picker, price_list in self._pickers():
			self.assertEqual(price_list, EMPTY, picker)
		self._set_defaults(DISABLED, EMPTY)
		for picker, price_list in self._pickers():
			self.assertEqual(price_list, EMPTY, picker)

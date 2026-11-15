"""An item with no selling price shows 0, never its valuation rate.

The till used to offer the valuation rate as the price of an unpriced item, which showed
cost to cashiers who are not meant to see it ('Hide Cost Price'). With 0, the usual checks
stop the sale: no zero-rate sales unless the till allows them, no selling below valuation.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from klik_pos.api.item.item_details import get_item_uoms_and_prices
from klik_pos.testing import any_till_currency
from klik_pos.api.item.item_price import fetch_item_price

ITEM = "_Klik Unpriced Item"
VALUATION = 55.0


class TestAnUnpricedItem(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		any_till_currency(cls)
		frappe.set_user("Administrator")
		if not frappe.db.exists("Item", ITEM):
			item = frappe.new_doc("Item")
			item.item_code = item.item_name = ITEM
			item.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
			item.stock_uom = "Nos"
			item.is_stock_item = 1
			item.valuation_rate = VALUATION
			item.append("uoms", {"uom": "Box", "conversion_factor": 10})
			item.insert(ignore_permissions=True)
		for name in frappe.get_all("Item Price", filters={"item_code": ITEM}, pluck="name"):
			frappe.delete_doc("Item Price", name, force=True)

	def test_the_fixture_has_a_valuation_and_no_price(self):
		self.assertEqual(flt(frappe.db.get_value("Item", ITEM, "valuation_rate")), VALUATION)
		self.assertFalse(frappe.db.exists("Item Price", {"item_code": ITEM}))

	def test_its_price_is_zero_on_any_price_list(self):
		self.assertEqual(fetch_item_price(ITEM)["price"], 0)
		self.assertEqual(fetch_item_price(ITEM, price_list="Standard Selling")["price"], 0)

	def test_and_in_another_uom(self):
		self.assertEqual(fetch_item_price(ITEM, uom="Box")["price"], 0)
		self.assertEqual(fetch_item_price(ITEM, price_list="Standard Selling", uom="Box")["price"], 0)

	def test_the_uom_list_prices_it_at_zero(self):
		uoms = get_item_uoms_and_prices(ITEM)["uoms"]
		self.assertTrue(uoms)
		self.assertEqual({u["uom"]: u["price"] for u in uoms}, {u["uom"]: 0 for u in uoms})

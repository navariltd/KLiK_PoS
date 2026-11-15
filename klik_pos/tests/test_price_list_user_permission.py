"""A Price List User Permission restricts the POS to those lists, as a Warehouse one restricts
the warehouse picker.

The picker offers only permitted lists; the default list (customer, group, till, Selling
Settings) skips lists the user may not use and falls back to one they may; an explicit list
the user may not use is refused; the per-line switch menu leaves such lists out. A user with
no such permission sees every enabled selling list.
"""

from unittest.mock import patch

import frappe
from erpnext.stock.doctype.item.test_item import make_item
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.item.item_details import get_full_pricing_and_batch_details
from klik_pos.api.item.item_price import (
	checked_price_list,
	fetch_item_price,
	get_price_list_with_customer_priority,
)
from klik_pos.api.item.pricing import get_selling_price_lists

USER = "price-list-perm@example.com"
ALLOWED = "_Test KLiK PL Allowed"
OTHER = "_Test KLiK PL Other"
# Sorts after ALLOWED: the default must win on being the default, not on its name.
BRANCH_RETAIL = "_Test KLiK PL Branch Retail"
ITEM = "_Test KLiK PL Perm Item"
CUSTOMER = "_Test KLiK PL Perm Customer"


class TestPriceListUserPermission(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		for name in (ALLOWED, OTHER, BRANCH_RETAIL):
			if not frappe.db.exists("Price List", name):
				frappe.get_doc(
					{"doctype": "Price List", "price_list_name": name, "selling": 1, "currency": "INR"}
				).insert()
			frappe.db.set_value("Price List", name, "enabled", 1)
		make_item(ITEM, {"is_stock_item": 1, "stock_uom": "Nos"})
		for price_list, rate in ((ALLOWED, 100), (OTHER, 70)):
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
		if not frappe.db.exists("Customer", CUSTOMER):
			frappe.get_doc({"doctype": "Customer", "customer_name": CUSTOMER}).insert()
		frappe.db.set_value("Customer", CUSTOMER, "default_price_list", OTHER)
		if not frappe.db.exists("User", USER):
			frappe.get_doc(
				{"doctype": "User", "email": USER, "first_name": "Price List", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", USER).add_roles("Sales User", "Stock User", "Accounts User", "Sales Master Manager")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.delete("Item Price", {"item_code": ITEM})
		frappe.delete_doc("Customer", CUSTOMER, force=True, ignore_missing=True)
		frappe.delete_doc("Item", ITEM, force=True, ignore_missing=True)
		for name in (ALLOWED, OTHER, BRANCH_RETAIL):
			frappe.delete_doc("Price List", name, force=True, ignore_missing=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		# The till prices from the list the restricted user may not use.
		till = frappe._dict(name="T", company="Dev Co", selling_price_list=OTHER)
		for module in ("item_price", "item_details"):
			p = patch(f"klik_pos.api.item.{module}.get_current_pos_profile", return_value=till)
			p.start()
			self.addCleanup(p.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("User Permission", {"user": USER, "allow": "Price List"})
		frappe.clear_cache(user=USER)

	def _restrict_to(self, price_list, is_default=0):
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": USER,
				"allow": "Price List",
				"for_value": price_list,
				"is_default": is_default,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=USER)
		frappe.set_user(USER)

	def _offered(self):
		return [row["name"] for row in get_selling_price_lists()["price_lists"]]

	def test_an_unrestricted_user_sees_every_enabled_selling_list(self):
		frappe.set_user(USER)
		self.assertIn(ALLOWED, self._offered())
		self.assertIn(OTHER, self._offered())

	def test_a_restricted_user_sees_only_the_allowed_lists(self):
		self._restrict_to(ALLOWED)
		self.assertEqual(self._offered(), [ALLOWED])

	def test_an_unrestricted_user_keeps_the_customer_and_till_defaults(self):
		frappe.set_user(USER)
		self.assertEqual(get_price_list_with_customer_priority(CUSTOMER), OTHER)
		self.assertEqual(get_price_list_with_customer_priority(None), OTHER)

	def test_the_default_falls_back_to_a_list_the_user_may_use(self):
		self._restrict_to(ALLOWED)
		self.assertEqual(get_price_list_with_customer_priority(CUSTOMER), ALLOWED)
		self.assertEqual(get_price_list_with_customer_priority(None), ALLOWED)

	def test_the_fallback_is_the_users_default_list(self):
		frappe.set_user("Administrator")
		self._restrict_to(ALLOWED)
		frappe.set_user("Administrator")
		self._restrict_to(BRANCH_RETAIL, is_default=1)
		self.assertEqual(get_price_list_with_customer_priority(CUSTOMER), BRANCH_RETAIL)
		self.assertEqual(get_price_list_with_customer_priority(None), BRANCH_RETAIL)

	def test_an_explicit_list_the_user_may_not_use_is_refused(self):
		self._restrict_to(ALLOWED)
		self.assertEqual(checked_price_list(ALLOWED), ALLOWED)
		self.assertIsNone(checked_price_list(None))
		with self.assertRaises(frappe.PermissionError):
			checked_price_list(OTHER)
		with self.assertRaises(frappe.PermissionError):
			fetch_item_price(ITEM, price_list=OTHER)

	def test_the_switch_menu_lists_price_lists_alphabetically(self):
		frappe.set_user(USER)
		names = [p["price_list"] for p in get_full_pricing_and_batch_details(ITEM)["price_lists"]]
		self.assertGreater(len(names), 1)
		self.assertEqual(names, sorted(names, key=str.lower))

	def test_the_switch_menu_leaves_out_lists_the_user_may_not_use(self):
		frappe.set_user(USER)
		offered = {p["price_list"] for p in get_full_pricing_and_batch_details(ITEM)["price_lists"]}
		self.assertTrue({ALLOWED, OTHER} <= offered)
		self._restrict_to(ALLOWED)
		offered = {p["price_list"] for p in get_full_pricing_and_batch_details(ITEM)["price_lists"]}
		self.assertIn(ALLOWED, offered)
		self.assertNotIn(OTHER, offered)

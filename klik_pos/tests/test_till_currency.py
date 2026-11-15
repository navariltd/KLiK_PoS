"""A till sells in one currency: its first mode of payment's. Price lists and customers in
another currency are not offered (a 4,850 INR price must never be charged as 4,850 KES)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.customer import get_customers
from klik_pos.api.item.item_details import get_full_pricing_and_batch_details
from klik_pos.api.item.item_price import checked_price_list, is_allowed_price_list
from klik_pos.api.item.pricing import get_selling_price_lists
from klik_pos.klik_pos.utils import till_currency
from klik_pos.tests.test_opening_conflict import COMPANY, _profile, _shift, _user

CASHIER = "klik-till-currency-cashier@example.com"
ITEM = "_KTC Item"
LOCAL_LIST, USD_LIST = "_KTC Local List", "_KTC USD List"
LOCAL_CUSTOMER, USD_CUSTOMER = "_KTC Local Customer", "_KTC USD Customer"
USD_MODE = "_KTC USD Cash"


def _usd_mode():
	"""A mode of payment whose Dev Co account is in USD."""
	account = frappe.db.get_value("Account", {"account_name": USD_MODE, "company": COMPANY})
	if not account:
		parent = frappe.db.get_value(
			"Account", {"company": COMPANY, "is_group": 1, "root_type": "Asset"}, order_by="lft desc"
		)
		account = frappe.get_doc(
			{"doctype": "Account", "account_name": USD_MODE, "parent_account": parent, "company": COMPANY,
			 "account_currency": "USD", "account_type": "Cash"}
		).insert(ignore_permissions=True).name
	if not frappe.db.exists("Mode of Payment", USD_MODE):
		frappe.get_doc(
			{"doctype": "Mode of Payment", "mode_of_payment": USD_MODE, "type": "Cash",
			 "accounts": [{"company": COMPANY, "default_account": account}]}
		).insert(ignore_permissions=True)
	return USD_MODE


class TestTillCurrency(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.local = frappe.db.get_value("Company", COMPANY, "default_currency")
		for name, currency in ((LOCAL_LIST, cls.local), (USD_LIST, "USD")):
			if not frappe.db.exists("Price List", name):
				frappe.get_doc(
					{"doctype": "Price List", "price_list_name": name, "selling": 1, "enabled": 1, "currency": currency}
				).insert(ignore_permissions=True)
		if not frappe.db.exists("Item", ITEM):
			frappe.get_doc(
				{"doctype": "Item", "item_code": ITEM, "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 0}
			).insert(ignore_permissions=True)
		for price_list, rate in ((LOCAL_LIST, 450), (USD_LIST, 4850)):
			if not frappe.db.exists("Item Price", {"item_code": ITEM, "price_list": price_list}):
				frappe.get_doc(
					{"doctype": "Item Price", "item_code": ITEM, "price_list": price_list, "price_list_rate": rate}
				).insert(ignore_permissions=True)
			# Back-dated: the test site runs on India time, where "today" may already be tomorrow.
			frappe.db.set_value("Item Price", {"item_code": ITEM, "price_list": price_list}, "valid_from", "2026-01-01")
		for name, currency in ((LOCAL_CUSTOMER, None), (USD_CUSTOMER, "USD")):
			if not frappe.db.exists("Customer", name):
				frappe.get_doc(
					{"doctype": "Customer", "customer_name": name, "customer_type": "Individual",
					 "default_currency": currency}
				).insert(ignore_permissions=True)
		_user(CASHIER)
		# A cashier's roles: reads the till's POS Profile (Accounts User) and Item Prices (Sales Master Manager).
		frappe.get_doc("User", CASHIER).add_roles("Sales User", "Stock User", "Accounts User", "Sales Master Manager")
		cls.usd_mode = _usd_mode()
		cls.till = _profile()  # payments: Cash, in the company's currency
		cls.shift = _shift(cls.till, CASHIER)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.set_value("POS Opening Entry", cls.shift, "docstatus", 2)
		frappe.delete_doc("POS Opening Entry", cls.shift, force=True, ignore_permissions=True)
		frappe.delete_doc("POS Profile", cls.till, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user(CASHIER)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _usd_first(self):
		frappe.set_user("Administrator")
		till = frappe.get_doc("POS Profile", self.till)
		till.payments = []
		till.append("payments", {"mode_of_payment": self.usd_mode, "default": 1})
		till.append("payments", {"mode_of_payment": "Cash"})
		till.flags.ignore_validate = True
		till.flags.ignore_mandatory = True
		till.save(ignore_permissions=True)
		frappe.set_user(CASHIER)

	def test_the_till_sells_in_its_first_payment_modes_currency(self):
		self.assertEqual(till_currency(), self.local)
		self._usd_first()
		self.assertEqual(till_currency(), "USD")

	def test_the_price_list_menu_offers_only_the_tills_currency(self):
		offered = {row.name for row in get_selling_price_lists()["price_lists"]}
		self.assertIn(LOCAL_LIST, offered)
		self.assertNotIn(USD_LIST, offered)
		self._usd_first()
		offered = {row.name for row in get_selling_price_lists()["price_lists"]}
		self.assertIn(USD_LIST, offered)
		self.assertNotIn(LOCAL_LIST, offered)

	def test_the_rate_popup_offers_only_the_tills_currency(self):
		offered = {p["price_list"] for p in get_full_pricing_and_batch_details(ITEM)["price_lists"]}
		self.assertIn(LOCAL_LIST, offered)
		self.assertNotIn(USD_LIST, offered)

	def test_a_list_in_another_currency_cannot_be_used(self):
		self.assertTrue(is_allowed_price_list(LOCAL_LIST))
		self.assertFalse(is_allowed_price_list(USD_LIST))
		self.assertEqual(checked_price_list(LOCAL_LIST), LOCAL_LIST)
		with self.assertRaises(frappe.PermissionError):
			checked_price_list(USD_LIST)

	def test_the_customer_list_offers_only_customers_billed_in_the_tills_currency(self):
		names = {c["name"] for c in get_customers(limit=500, search="_KTC")["data"]}
		self.assertIn(LOCAL_CUSTOMER, names)  # no billing currency: billed in the company's
		self.assertNotIn(USD_CUSTOMER, names)
		self._usd_first()
		names = {c["name"] for c in get_customers(limit=500, search="_KTC")["data"]}
		self.assertIn(USD_CUSTOMER, names)
		self.assertNotIn(LOCAL_CUSTOMER, names)

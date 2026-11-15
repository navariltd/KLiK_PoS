"""The invoice and the held order record the price lists the cashier actually used.

Bug this fixes (POS-06134 on dev): the cart was switched to another list, the line was priced
from it, and the invoice still saved the till's default list, because _set_pos_profile_fields
resolved the list from customer -> group -> till and ignored the cart. The minimum selling
price (cecypo_powerpack) judges a till sale against that list, so it has to be right.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from klik_pos.api.sales_invoice import queue_sales_invoice
from klik_pos.api.sales_order import create_held_order, get_held_order_details
from klik_pos.tests.test_opening_conflict import _profile, _shift, _user

CASHIER = "klik-price-list-cashier@example.com"
CUSTOMER = "_Test Customer"
ITEM = "_KPL Item"
RETAIL, ELITE, OTHER = "_KPL Retail", "_KPL Elite", "_KPL Other"
LINE_FIELD = "powerpack_line_price_list"


def _ensure_fixtures():
	for name in (RETAIL, ELITE, OTHER):
		if not frappe.db.exists("Price List", name):
			frappe.get_doc(
				{"doctype": "Price List", "price_list_name": name, "selling": 1, "enabled": 1,
				 "currency": frappe.db.get_value("Company", "Dev Co", "default_currency")}
			).insert(ignore_permissions=True)
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc(
			{"doctype": "Item", "item_code": ITEM, "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 0}
		).insert(ignore_permissions=True)
	for price_list, rate in ((RETAIL, 450), (ELITE, 400), (OTHER, 300)):
		if not frappe.db.exists("Item Price", {"item_code": ITEM, "price_list": price_list}):
			frappe.get_doc(
				{"doctype": "Item Price", "item_code": ITEM, "price_list": price_list, "price_list_rate": rate}
			).insert(ignore_permissions=True)
	_user(CASHIER)
	user = frappe.get_doc("User", CASHIER)
	# Accounts User: ERPNext's get_pos_profile needs read on the till's POS Profile, as a real cashier has.
	missing = {"Sales User", "Accounts User"} - {row.role for row in user.roles}
	if missing:
		user.extend("roles", [{"role": role} for role in sorted(missing)])
		user.save(ignore_permissions=True)
	for price_list, is_default in ((RETAIL, 1), (ELITE, 0)):
		if not frappe.db.exists("User Permission", {"user": CASHIER, "allow": "Price List", "for_value": price_list}):
			frappe.get_doc(
				{"doctype": "User Permission", "user": CASHIER, "allow": "Price List", "for_value": price_list,
				 "is_default": is_default}
			).insert(ignore_permissions=True)


class TestPriceListRecording(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_fixtures()
		cls.till = _profile()
		frappe.db.set_value(
			"POS Profile",
			cls.till,
			{"selling_price_list": RETAIL, "allow_partial_payment": 1, "custom_allow_credit_sales_as_pos": 1},
		)
		cls.shift = _shift(cls.till, CASHIER)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		if frappe.db.exists("POS Opening Entry", cls.shift):
			frappe.db.set_value("POS Opening Entry", cls.shift, "docstatus", 2)
			frappe.delete_doc("POS Opening Entry", cls.shift, force=True, ignore_permissions=True)
		for name in frappe.get_all("Sales Invoice", {"pos_profile": cls.till}, pluck="name"):
			frappe.db.delete("Sales Invoice Item", {"parent": name})
			for ledger in ("GL Entry", "Payment Ledger Entry"):
				frappe.db.delete(ledger, {"voucher_type": "Sales Invoice", "voucher_no": name})
			frappe.db.delete("Sales Invoice", name)
		frappe.delete_doc("POS Profile", cls.till, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user(CASHIER)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _payload(self, header=None, line_list=None, rate=420):
		payload = {
			"customer": {"id": CUSTOMER},
			"items": [{"id": ITEM, "item_code": ITEM, "quantity": 1, "price": rate, "uom": "Nos"}],
			"isCreditSale": True,
			"dueDate": add_days(nowdate(), 7),
			"checkout_request_id": frappe.generate_hash(length=24),
			"businessType": "B2C",
			"enable_background_invoice_submission": 1,
		}
		if header:
			payload["selling_price_list"] = header
		if line_list:
			payload["itemDiscounts"] = {ITEM: {"selectedPriceList": line_list}}
		return payload

	def _invoice(self, payload):
		result = queue_sales_invoice(payload)
		self.assertTrue(result.get("success"), result.get("message"))
		return frappe.get_doc("Sales Invoice", result["invoice_name"])

	def test_invoice_records_cart_list(self):
		self.assertEqual(self._invoice(self._payload(header=ELITE)).selling_price_list, ELITE)

	def test_disallowed_cart_list_falls_back(self):
		self.assertEqual(self._invoice(self._payload(header=OTHER)).selling_price_list, RETAIL)

	def test_no_list_sent_keeps_chain(self):
		self.assertEqual(self._invoice(self._payload()).selling_price_list, RETAIL)

	def test_line_list_recorded(self):
		if not frappe.get_meta("Sales Invoice Item").has_field(LINE_FIELD):
			self.skipTest("cecypo_powerpack line price list field not installed")
		doc = self._invoice(self._payload(header=RETAIL, line_list=ELITE))
		self.assertEqual(doc.items[0].get(LINE_FIELD), ELITE)

	def test_line_list_sent_on_the_item(self):
		if not frappe.get_meta("Sales Invoice Item").has_field(LINE_FIELD):
			self.skipTest("cecypo_powerpack line price list field not installed")
		payload = self._payload(header=RETAIL)
		payload["items"][0]["selectedPriceList"] = ELITE
		self.assertEqual(self._invoice(payload).items[0].get(LINE_FIELD), ELITE)

	def test_disallowed_line_list_not_recorded(self):
		if not frappe.get_meta("Sales Invoice Item").has_field(LINE_FIELD):
			self.skipTest("cecypo_powerpack line price list field not installed")
		doc = self._invoice(self._payload(header=RETAIL, line_list=OTHER))
		self.assertFalse(doc.items[0].get(LINE_FIELD))

	def test_held_order_keeps_lists_through_resume(self):
		payload = self._payload(header=ELITE, line_list=ELITE)
		payload["status"] = "held"
		held = create_held_order(payload)
		self.assertTrue(held.get("success"), held.get("message"))
		self.assertEqual(frappe.db.get_value("Sales Order", held["order_name"], "selling_price_list"), ELITE)
		details = get_held_order_details(held["order_name"])
		self.assertEqual(details["selling_price_list"], ELITE)
		self.assertEqual(details["items"][0]["selectedPriceList"], ELITE)

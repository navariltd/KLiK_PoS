"""Invoice History has one search box. It matches every column the table shows - number,
customer, cashier, amount, date, status - and a row must match every word typed."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, nowdate

from klik_pos.api import payment, quotation
from klik_pos.api.sales_invoice import _matches_search, get_sales_invoices
from klik_pos.api.sales_order import get_held_orders
from klik_pos.setup.quote_fields import ensure_quote_fields
from klik_pos.tests.test_held_order_visibility import CUSTOMER, ITEM, _held_order
from klik_pos.tests.test_opening_conflict import _profile, _shift
from klik_pos.tests.test_till_quote import _payload

AMOUNT = 7341


class TestMatchesSearch(FrappeTestCase):
	def test_every_word_must_be_found_in_some_value(self):
		row = ("SO-0001", "Acme Ltd", "Jane Cashier", "1500.00")
		self.assertTrue(_matches_search("acme 1500", *row))
		self.assertTrue(_matches_search("1,500 jane", *row))
		self.assertFalse(_matches_search("acme 999", *row))
		self.assertTrue(_matches_search("", *row))


class TestHistorySearch(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_quote_fields()
		self.addCleanup(frappe.db.rollback)
		_shift(_profile(), "Administrator")
		self.cashier = frappe.db.get_value("User", "Administrator", "full_name")
		self.today = getdate(nowdate())

	def _searches(self):
		return (
			str(AMOUNT), f"{AMOUNT:,}", self.cashier.split()[0], self.today.strftime("%Y-%m-%d"),
			self.today.strftime("%b"), f"{CUSTOMER.split()[0]} {AMOUNT}",
		)

	def test_invoices(self):
		si = frappe.new_doc("Sales Invoice")
		si.customer = CUSTOMER
		si.company = frappe.db.get_value("POS Profile", _profile(), "company")
		si.append("items", {"item_code": ITEM, "qty": 1, "rate": AMOUNT})
		si.insert(ignore_permissions=True, ignore_mandatory=True)
		self.assertEqual(si.grand_total, AMOUNT)

		def found(search):
			res = get_sales_invoices(limit=500, search=search, skip_opening_entry_filter=True, surface="history")
			self.assertTrue(res["success"], res)
			return si.name in [r["name"] for r in res["data"]]

		for search in (*self._searches(), "draft"):
			self.assertTrue(found(search), search)
		self.assertFalse(found(f"{CUSTOMER.split()[0]} 99999123"))

	def test_outstanding_invoices_on_the_payments_page(self):
		si = frappe.new_doc("Sales Invoice")
		si.customer = CUSTOMER
		si.company = frappe.db.get_value("POS Profile", _profile(), "company")
		si.append("items", {"item_code": ITEM, "qty": 1, "rate": AMOUNT})
		si.insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value(
			"Sales Invoice", si.name,
			{"docstatus": 1, "outstanding_amount": AMOUNT, "status": "Overdue"}, update_modified=False,
		)

		def listed(**kwargs):
			res = payment.get_outstanding_sales_invoices(limit=500, **kwargs)
			self.assertTrue(res["success"], res)
			return res

		def found(**kwargs):
			return si.name in [r["name"] for r in listed(**kwargs)["data"]]

		for search in (*self._searches(), "overdue"):
			if search != self.cashier.split()[0]:  # the table has no cashier column
				self.assertTrue(found(search=search), search)
		self.assertFalse(found(search=f"{CUSTOMER.split()[0]} 99999123"))

		today, tomorrow = self.today.strftime("%Y-%m-%d"), frappe.utils.add_days(self.today, 1)
		self.assertTrue(found(from_date=today, to_date=today))
		self.assertFalse(found(from_date=tomorrow))
		self.assertTrue(found(status="Overdue"))
		self.assertFalse(found(status="Unpaid"))
		# The dropdown's choices: every status the other filters leave, not just the chosen one.
		self.assertIn("Overdue", listed(status="Unpaid")["statuses"])
		self.assertNotIn("Overdue", listed(from_date=tomorrow)["statuses"])

	def test_held_orders(self):
		so = _held_order()
		frappe.db.set_value("Sales Order", so.name, "grand_total", AMOUNT, update_modified=False)

		def found(search):
			res = get_held_orders(skip_opening_entry_filter=True, limit=500, search=search)
			self.assertTrue(res["success"], res)
			return so.name in [r["name"] for r in res["data"]]

		for search in (*self._searches(), "held"):
			self.assertTrue(found(search), search)
		self.assertFalse(found(f"{CUSTOMER.split()[0]} 99999123"))

	def test_quotes(self):
		res = quotation.create_quote(_payload(ITEM, qty=1, rate=AMOUNT))
		self.assertTrue(res["success"], res)
		name = res["quote_name"]
		self.assertEqual(frappe.db.get_value("Quotation", name, "grand_total"), AMOUNT)

		def found(search):
			res = quotation.get_quotes(limit=500, search=search)
			self.assertTrue(res["success"], res)
			return name in [r["name"] for r in res["data"]]

		for search in (str(AMOUNT), f"{AMOUNT:,}", self.today.strftime("%Y-%m-%d"), self.today.strftime("%b"), f"{CUSTOMER.split()[0]} {AMOUNT}"):
			self.assertTrue(found(search), search)
		self.assertFalse(found(f"{CUSTOMER.split()[0]} 99999123"))

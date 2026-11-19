"""Re-holding a recalled order must replace it, not leave a second one behind.

Customers found duplicate Sales Orders after recalling a held order, editing it and holding
it again. The server has always supported replacement - pass `held_order_id` and it rebuilds
that order in place - so this pins the contract the client depends on: with the id, one
order; without it, a new one.

The client half of the same bug was that the id was read through a five-minute cache, so an
edit that ran longer sent nothing and got the "without it" branch. That is covered by
klik_spa/src/utils/draftInvoiceCache.test.ts.
"""

import frappe
from frappe.utils import add_days, nowdate
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_order import create_held_order

CUSTOMER = "Walk In"
ITEM = "Consulting"


def _payload(qty, held_order_id=None):
	return {
		"customer": {"id": CUSTOMER},
		"items": [
			{
				"id": ITEM,
				"item_code": ITEM,
				"name": ITEM,
				"quantity": qty,
				"price": 100,
				"uom": "Nos",
			}
		],
		"status": "held",
		"held_order_id": held_order_id,
	}


def _held_count():
	return frappe.db.count("Sales Order", {"custom_is_klik_held": 1, "docstatus": 0})


class TestReHoldingReplaces(FrappeTestCase):
	def test_holding_again_with_the_id_updates_the_same_order(self):
		"""The regression: this is what "edited and held again" must do."""
		first = create_held_order(_payload(qty=1))
		self.assertTrue(first["success"], msg=first.get("message"))
		before = _held_count()

		second = create_held_order(_payload(qty=5, held_order_id=first["order_name"]))

		self.assertTrue(second["success"], msg=second.get("message"))
		self.assertEqual(second["order_name"], first["order_name"], "a second order was created")
		self.assertEqual(_held_count(), before, "the held-order count grew")
		self.assertEqual(frappe.db.get_value("Sales Order", first["order_name"], "total_qty"), 5)

	def test_holding_again_dates_the_order_today(self):
		first = create_held_order(_payload(qty=1))
		# Held on an earlier day, recalled and held again today.
		frappe.db.set_value("Sales Order", first["order_name"], {"transaction_date": add_days(nowdate(), -3), "delivery_date": add_days(nowdate(), -3)})

		create_held_order(_payload(qty=2, held_order_id=first["order_name"]))

		self.assertEqual(str(frappe.db.get_value("Sales Order", first["order_name"], "transaction_date")), nowdate())

	def test_holding_without_an_id_creates_a_second_order(self):
		"""The other side of the contract, and exactly what the expired cache was causing."""
		first = create_held_order(_payload(qty=1))
		before = _held_count()

		second = create_held_order(_payload(qty=1))

		self.assertNotEqual(second["order_name"], first["order_name"])
		self.assertEqual(_held_count(), before + 1)

	def test_a_submitted_order_is_refused_rather_than_rewritten(self):
		first = create_held_order(_payload(qty=1))
		so = frappe.get_doc("Sales Order", first["order_name"])
		so.submit()

		result = create_held_order(_payload(qty=2, held_order_id=so.name))

		self.assertFalse(result["success"])
		self.assertIn("no longer a draft", result["message"])

	def test_holding_again_redates_the_payment_schedule(self):
		"""Held on an earlier day, its schedule row kept that day's due date: re-holding dated the
		order today and ERPNext refused "Due Date ... cannot be before Posting Date"."""
		first = create_held_order(_payload(qty=1))
		earlier = add_days(nowdate(), -3)
		name = first["order_name"]
		frappe.db.set_value("Sales Order", name, {"transaction_date": earlier, "delivery_date": earlier})
		frappe.db.set_value("Payment Schedule", {"parent": name, "parenttype": "Sales Order"}, "due_date", earlier)

		second = create_held_order(_payload(qty=2, held_order_id=name))

		self.assertTrue(second["success"], msg=second.get("message"))
		due = frappe.get_all("Payment Schedule", filters={"parent": name, "parenttype": "Sales Order"}, pluck="due_date")
		self.assertTrue(due and all(str(d) >= nowdate() for d in due), due)

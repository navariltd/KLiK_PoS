"""Merging held orders: the cart stands for order A and carries order B's lines.

B is retired - deleted - by whatever finishes A: holding it again, checking it out, or an
M-Pesa submit. B is checked before any of that work starts, so a B that was sold elsewhere
refuses the sale instead of selling its lines twice.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api import mpesa_order
from klik_pos.api.sales_order import checkout_held_order, create_held_order
from klik_pos.tests.test_held_order_replace import CUSTOMER, _payload


def _hold(qty=1):
	res = create_held_order(_payload(qty=qty))
	assert res["success"], res
	return res["order_name"]


def _merged(qty, into, merged):
	p = _payload(qty=qty, held_order_id=into)
	p["merged_held_order_ids"] = merged
	return p


class TestHoldRetiresMergedOrders(FrappeTestCase):
	def test_holding_the_merged_cart_retires_the_merged_order(self):
		a, b = _hold(1), _hold(2)
		res = create_held_order(_merged(3, a, [b]))
		self.assertTrue(res["success"], res)
		self.assertEqual(res["order_name"], a)
		self.assertEqual(frappe.db.get_value("Sales Order", a, "total_qty"), 3)
		self.assertFalse(frappe.db.exists("Sales Order", b))

	def test_a_merged_order_gone_elsewhere_refuses_the_hold_and_leaves_a_as_it_was(self):
		a, b = _hold(1), _hold(2)
		frappe.delete_doc("Sales Order", b, force=1, ignore_permissions=True)
		res = create_held_order(_merged(3, a, [b]))
		self.assertFalse(res["success"])
		self.assertIn(b, res["message"])
		self.assertNotEqual(res.get("code"), "held_order_gone", "that code would make the POS drop A")
		# A is left as it was by create_held_order's full rollback on any refusal.

	def test_an_mpesa_order_is_not_retired_by_a_merge(self):
		a, b = _hold(1), _hold(2)
		frappe.db.set_value("Sales Order", b, "custom_klik_mpesa_order", 1)
		res = create_held_order(_merged(3, a, [b]))
		self.assertFalse(res["success"])
		self.assertIn("M-Pesa", res["message"])

	def test_the_order_itself_in_the_merged_list_is_ignored(self):
		a = _hold(1)
		res = create_held_order(_merged(2, a, [a]))
		self.assertTrue(res["success"], res)
		self.assertTrue(frappe.db.exists("Sales Order", a))


class TestHeldCheckoutHandsMergedOrdersOn(FrappeTestCase):
	def test_held_checkout_passes_the_merged_orders_to_the_invoice(self):
		"""The invoice route claims and deletes them (TestPlainCheckoutRetiresMergedOrders runs
		it for real); the held checkout only has to pass them on, with its own order excluded."""
		a, b = _hold(1), _hold(2)
		invoice = MagicMock(return_value={"success": True})
		with patch("klik_pos.api.sales_invoice._queue_sales_invoice", invoice):
			res = checkout_held_order(a, {"checkout_request_id": None, "merged_held_order_ids": [b]})
		self.assertTrue(res["success"], res)
		data = invoice.call_args.args[0]
		self.assertEqual(data["merged_held_order_ids"], [b])
		self.assertEqual(invoice.call_args.kwargs["source_order"], a)


class TestMpesaSubmitRetiresMergedOrders(FrappeTestCase):
	def _submit(self, order, b):
		draft = MagicMock(return_value="ACC-SINV-DRAFT")
		with (
			patch.object(mpesa_order, "_invoice_already_made", return_value=None),
			patch.object(mpesa_order, "_load", return_value=frappe._dict(docstatus=0)),
			patch.object(mpesa_order, "_waiting", return_value=[]),
			patch.object(mpesa_order, "_paid_push_refusal", return_value=None),
			patch.object(mpesa_order, "_create_invoice_draft", draft),
			patch.object(mpesa_order, "_submit_draft_invoice", return_value={"success": True, "invoice_name": "X"}),
			patch.object(mpesa_order, "_hand_over_to_invoice"),
		):
			res = mpesa_order.submit_mpesa_order(order, {"merged_held_order_ids": [b]})
		return res, draft

	def test_mpesa_submit_retires_the_merged_order(self):
		a, b = _hold(1), _hold(2)
		res, _draft = self._submit(a, b)
		self.assertTrue(res["success"], res)
		self.assertFalse(frappe.db.exists("Sales Order", b))

	def test_a_merged_order_gone_elsewhere_refuses_before_the_draft(self):
		a, b = _hold(1), _hold(2)
		frappe.delete_doc("Sales Order", b, force=1, ignore_permissions=True)
		res, draft = self._submit(a, b)
		self.assertFalse(res["success"])
		draft.assert_not_called()


class TestAFailedDeleteRefusesTheSale(FrappeTestCase):
	def test_a_merged_order_that_will_not_delete_refuses_the_hold(self):
		"""Swallowed, the hold would commit with B still held and its lines already in A."""
		a, b = _hold(1), _hold(2)
		real = frappe.delete_doc

		def refuse_b(doctype, name, *args, **kwargs):
			if name == b:
				raise frappe.LinkExistsError(f"{b} is linked")
			return real(doctype, name, *args, **kwargs)

		with patch("frappe.delete_doc", side_effect=refuse_b):
			res = create_held_order(_merged(3, a, [b]))
		self.assertFalse(res["success"], res)


from klik_pos.api.sales_invoice import queue_sales_invoice  # noqa: E402
from klik_pos.tests import test_checkout_idempotency as _ci  # noqa: E402


class TestPlainCheckoutRetiresMergedOrders(_ci.TestCheckoutEndToEnd):
	"""The cart's own order gone (checked out at another till), its merged lines rung up as a
	new sale: the merged order must still be retired, or it is sold again later."""

	def test_a_plain_sale_retires_the_merged_order(self):
		b = _hold(2)
		payload = {**self._payload(), "merged_held_order_ids": [b]}
		res = queue_sales_invoice(payload)
		self.assertTrue(res["success"], res)
		self.assertFalse(frappe.db.exists("Sales Order", b))

	def test_a_plain_sale_with_a_merged_order_gone_is_refused(self):
		b = _hold(2)
		frappe.delete_doc("Sales Order", b, force=1, ignore_permissions=True)
		res = queue_sales_invoice({**self._payload(), "merged_held_order_ids": [b]})
		self.assertFalse(res["success"], res)


# Only the tests above: the parent's own run in test_checkout_idempotency.
for _name in dir(_ci.TestCheckoutEndToEnd):
	if _name.startswith("test"):
		setattr(TestPlainCheckoutRetiresMergedOrders, _name, None)


# --- Merges the earlier tests missed: orders held on different days, for different customers,
# carrying the same item. Re-holding dated the cart's order today but kept its payment schedule
# from the day it was held, so ERPNext refused the merge.
from frappe.utils import add_days, nowdate  # noqa: E402

OTHER_CUSTOMER = "_KTC Local Customer"


def _cart(customer, lines, held_order_id=None, merged=None):
	"""lines: (item_code, qty) - each one a row of its own, as the POS sends them."""
	p = _payload(qty=1, held_order_id=held_order_id)
	p["customer"] = {"id": customer}
	p["items"] = [
		{"id": f"{code}-{i}" if i else code, "item_code": code, "name": code, "quantity": qty, "price": 100, "uom": "Nos"}
		for i, (code, qty) in enumerate(lines)
	]
	if merged:
		p["merged_held_order_ids"] = merged
	return p


def _held_on(days_ago, customer, lines):
	res = create_held_order(_cart(customer, lines))
	assert res["success"], res
	name, day = res["order_name"], add_days(nowdate(), -days_ago)
	frappe.db.set_value("Sales Order", name, {"transaction_date": day, "delivery_date": day})
	frappe.db.set_value("Sales Order Item", {"parent": name}, "delivery_date", day)
	frappe.db.set_value("Payment Schedule", {"parent": name, "parenttype": "Sales Order"}, "due_date", day)
	return name


class TestMergeAcrossDaysCustomersAndItems(FrappeTestCase):
	def setUp(self):
		if not frappe.db.exists("Customer", OTHER_CUSTOMER):
			self.skipTest(f"{OTHER_CUSTOMER} missing on this site")

	def _assert_held_today(self, name):
		so = frappe.get_doc("Sales Order", name)
		self.assertEqual(str(so.transaction_date), nowdate())
		self.assertTrue(all(str(r.due_date) >= nowdate() for r in so.payment_schedule), so.payment_schedule)
		return so

	def test_older_cart_combines_a_newer_order_of_another_customer(self):
		a = _held_on(5, CUSTOMER, [("Consulting", 1)])
		b = _held_on(2, OTHER_CUSTOMER, [("Consulting", 2)])
		# Combined: one Consulting line of 3; the cashier kept B's customer.
		res = create_held_order(_cart(OTHER_CUSTOMER, [("Consulting", 3)], a, [b]))
		self.assertTrue(res["success"], res)
		so = self._assert_held_today(a)
		self.assertEqual(so.customer, OTHER_CUSTOMER)
		self.assertEqual([(r.item_code, r.qty) for r in so.items], [("Consulting", 3)])
		self.assertFalse(frappe.db.exists("Sales Order", b))

	def test_newer_cart_keeps_an_older_orders_same_item_as_separate_lines(self):
		a = _held_on(1, CUSTOMER, [("Consulting", 1)])
		b = _held_on(6, OTHER_CUSTOMER, [("Consulting", 2)])
		res = create_held_order(_cart(CUSTOMER, [("Consulting", 1), ("Consulting", 2)], a, [b]))
		self.assertTrue(res["success"], res)
		so = self._assert_held_today(a)
		self.assertEqual(so.customer, CUSTOMER)
		self.assertEqual(sorted(r.qty for r in so.items), [1, 2])
		self.assertEqual(so.total_qty, 3)
		self.assertFalse(frappe.db.exists("Sales Order", b))

	def test_three_orders_from_three_days_merge_into_the_oldest(self):
		a = _held_on(7, CUSTOMER, [("Consulting", 1)])
		b = _held_on(3, OTHER_CUSTOMER, [("Consulting", 2)])
		c = _held_on(0, CUSTOMER, [("Consulting", 4)])
		res = create_held_order(_cart(CUSTOMER, [("Consulting", 7)], a, [b, c]))
		self.assertTrue(res["success"], res)
		self.assertEqual(self._assert_held_today(a).total_qty, 7)
		self.assertFalse(frappe.db.exists("Sales Order", b))
		self.assertFalse(frappe.db.exists("Sales Order", c))


class TestCheckoutOfMergedOrdersAcrossDays(_ci.TestCheckoutEndToEnd):
	"""Checked out for real: an older order of another customer merged into a newer one, the
	same stock item in both."""

	def test_checkout_sells_the_merged_lines_and_retires_both_orders(self):
		a = _held_on(2, self.customer, [(_ci.ITEM_CODE, 1)])
		b = _held_on(5, OTHER_CUSTOMER if frappe.db.exists("Customer", OTHER_CUSTOMER) else CUSTOMER, [(_ci.ITEM_CODE, 2)])
		payload = self._payload()
		payload["items"][0]["quantity"] = 3
		total = _ci.payable_total(self.customer, payload["items"], self.payment_mode)
		payload.update(amountPaid=total, paymentMethods=[{"method": self.payment_mode, "amount": total}])
		payload["merged_held_order_ids"] = [b]

		res = checkout_held_order(a, payload)

		self.assertTrue(res["success"], res)
		self.addCleanup(self._remove_invoice, res["invoice_name"])
		inv = frappe.get_doc("Sales Invoice", res["invoice_name"])
		self.assertEqual(inv.customer, self.customer)
		self.assertEqual(str(inv.posting_date), nowdate())
		self.assertEqual(sum(r.qty for r in inv.items), 3)
		self.assertFalse(frappe.db.exists("Sales Order", a))
		self.assertFalse(frappe.db.exists("Sales Order", b))


for _name in dir(_ci.TestCheckoutEndToEnd):
	if _name.startswith("test"):
		setattr(TestCheckoutOfMergedOrdersAcrossDays, _name, None)

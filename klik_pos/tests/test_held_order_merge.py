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
from klik_pos.tests.test_held_order_replace import _payload


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


class TestCheckoutRetiresMergedOrders(FrappeTestCase):
	def test_checkout_retires_the_merged_order(self):
		a, b = _hold(1), _hold(2)
		with patch("klik_pos.api.sales_invoice._queue_sales_invoice", return_value={"success": True}):
			res = checkout_held_order(a, {"checkout_request_id": None, "merged_held_order_ids": [b]})
		self.assertTrue(res["success"], res)
		self.assertFalse(frappe.db.exists("Sales Order", b))

	def test_a_merged_order_gone_elsewhere_refuses_checkout_before_invoicing(self):
		a, b = _hold(1), _hold(2)
		frappe.delete_doc("Sales Order", b, force=1, ignore_permissions=True)
		invoice = MagicMock(return_value={"success": True})
		with patch("klik_pos.api.sales_invoice._queue_sales_invoice", invoice):
			res = checkout_held_order(a, {"checkout_request_id": None, "merged_held_order_ids": [b]})
		self.assertFalse(res["success"])
		invoice.assert_not_called()
		self.assertTrue(frappe.db.exists("Sales Order", a))


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

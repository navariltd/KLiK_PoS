"""A sale that never posted must keep asking for attention until someone deals with it.

The realtime alert is fire-and-forget: a cashier who reloaded, or who was serving the next
customer when it fired, never sees it. Rather than tracking delivery and acknowledgement, the
state is derived - a failed queued invoice is already durable, so the answer clears itself the
moment the invoice submits.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_invoice import QUEUE_STATUSES, get_unresolved_queue_failures

CASHIER = "unresolved_cashier@example.com"
OTHER_CASHIER = "unresolved_other@example.com"
MANAGER = "unresolved_manager@example.com"


def _ensure_user(email, roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": r} for r in roles],
			}
		).insert(ignore_permissions=True)
	return email


class TestUnresolvedQueueFailures(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_user(CASHIER, [])
		_ensure_user(OTHER_CASHIER, [])
		_ensure_user(MANAGER, ["Sales Manager"])

		reference = frappe.get_all(
			"Sales Invoice",
			filters={"docstatus": 1, "is_return": 0},
			fields=["company", "customer"],
			limit=1,
		)
		if not reference:
			raise cls.skipException("no posted Sales Invoice to borrow a company from")
		cls.company = reference[0].company
		cls.customer = reference[0].customer
		cls.item = frappe.db.get_value("Item", {"is_sales_item": 1, "disabled": 0}, "name")
		frappe.db.commit()

	def _failed_invoice(self, owner, status=None):
		doc = frappe.new_doc("Sales Invoice")
		doc.customer = self.customer
		doc.company = self.company
		doc.append("items", {"item_code": self.item, "qty": 1, "rate": 10})
		doc.insert(ignore_permissions=True)

		frappe.db.set_value(
			"Sales Invoice",
			doc.name,
			{
				"owner": owner,
				"queue_status": status or QUEUE_STATUSES["failed"],
				"queue_error": "Insufficient Permission for Stock Reservation Entry",
			},
			update_modified=False,
		)
		self.addCleanup(
			lambda name=doc.name: frappe.delete_doc(
				"Sales Invoice", name, force=True, ignore_permissions=True
			)
		)
		return doc.name

	def _as(self, user):
		original = frappe.session.user
		frappe.set_user(user)
		self.addCleanup(frappe.set_user, original)
		return get_unresolved_queue_failures()

	def test_a_cashier_sees_their_own_unresolved_sale(self):
		name = self._failed_invoice(CASHIER)

		result = self._as(CASHIER)

		self.assertTrue(result["success"])
		reported = {row["invoice_name"] for row in result["invoices"]}
		self.assertIn(name, reported)

	def test_the_reason_travels_with_it(self):
		self._failed_invoice(CASHIER)

		row = next(r for r in self._as(CASHIER)["invoices"])

		self.assertIn("Insufficient Permission", row["error"])

	def test_a_cashier_does_not_see_another_till(self):
		mine = self._failed_invoice(CASHIER)
		theirs = self._failed_invoice(OTHER_CASHIER)

		reported = {row["invoice_name"] for row in self._as(CASHIER)["invoices"]}

		self.assertIn(mine, reported)
		self.assertNotIn(theirs, reported)

	def test_a_manager_sees_every_till(self):
		"""Managers are the ones who can act on a till they are not standing at."""
		mine = self._failed_invoice(CASHIER)
		theirs = self._failed_invoice(OTHER_CASHIER)

		reported = {row["invoice_name"] for row in self._as(MANAGER)["invoices"]}

		self.assertIn(mine, reported)
		self.assertIn(theirs, reported)

	def test_a_submitted_invoice_clears_itself(self):
		"""No acknowledgement flow: the condition that raises it is the condition."""
		name = self._failed_invoice(CASHIER)
		self.assertIn(name, {r["invoice_name"] for r in self._as(CASHIER)["invoices"]})

		frappe.db.set_value(
			"Sales Invoice", name, "queue_status", QUEUE_STATUSES["submitted"], update_modified=False
		)

		self.assertNotIn(name, {r["invoice_name"] for r in self._as(CASHIER)["invoices"]})

	def test_a_queued_invoice_is_not_reported_as_failed(self):
		name = self._failed_invoice(CASHIER, status=QUEUE_STATUSES["queued"])

		self.assertNotIn(name, {r["invoice_name"] for r in self._as(CASHIER)["invoices"]})

	def test_a_clean_till_reports_nothing(self):
		result = self._as(OTHER_CASHIER)

		self.assertTrue(result["success"])
		self.assertEqual(result["count"], 0)

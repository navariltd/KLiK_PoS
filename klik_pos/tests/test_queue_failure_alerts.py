"""A failed queued invoice must reach supervisors live, not only by email.

Background submission returns HTTP 200 the moment the invoice is queued, so a later failure is
invisible at the counter. The realtime push closes that for the cashier; managers were still
left waiting on an email, which is the wrong latency for a sale that did not post.
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api import sales_invoice
from klik_pos.api.sales_invoice import (
	QUEUE_FAILURE_EVENT,
	_get_queue_failure_user_ids,
	_notify_queue_failure,
)

CASHIER = "queue_alert_cashier@example.com"
MANAGER = "queue_alert_manager@example.com"


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


class TestQueueFailureAudience(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Left in place rather than deleted: removing a User cascades into Contact/ToDo and
		# deadlocks across sequential test classes.
		_ensure_user(CASHIER, [])
		_ensure_user(MANAGER, ["Sales Manager"])
		frappe.db.commit()

	def test_the_audience_includes_the_cashier_and_managers(self):
		user_ids = _get_queue_failure_user_ids(CASHIER)

		self.assertIn(CASHIER, user_ids)
		self.assertIn(MANAGER, user_ids)

	def test_the_audience_has_no_duplicates(self):
		"""A manager who is also the cashier must not be alerted twice."""
		user_ids = _get_queue_failure_user_ids(MANAGER)

		self.assertEqual(len(user_ids), len(set(user_ids)))

	def test_a_disabled_user_is_not_alerted(self):
		frappe.db.set_value("User", MANAGER, "enabled", 0)
		self.addCleanup(frappe.db.set_value, "User", MANAGER, "enabled", 1)
		frappe.clear_cache()

		self.assertNotIn(MANAGER, _get_queue_failure_user_ids(CASHIER))

	def test_the_event_is_published_to_every_recipient(self):
		invoice = SimpleNamespace(name="TEST-QUEUE-ALERT-001", customer="ACME", customer_name="ACME Ltd")

		# frappe.get_doc is deliberately NOT patched: the audience resolution uses it too, so
		# stubbing it would make this assert against MagicMocks rather than real user ids.
		with (
			patch.object(sales_invoice.frappe, "publish_realtime") as publish,
			patch.object(sales_invoice, "_get_queue_failure_recipients", return_value=[]),
			patch.object(sales_invoice.frappe, "sendmail"),
		):
			_notify_queue_failure(invoice, CASHIER, "Insufficient Permission")

		# Inserting the Notification Log publishes its own realtime event, so filter to ours.
		ours = [c for c in publish.call_args_list if c.kwargs.get("event") == QUEUE_FAILURE_EVENT]
		self.assertTrue(ours, "the queue failure event was never published")

		targeted = {c.kwargs["user"] for c in ours}
		self.assertIn(CASHIER, targeted)
		self.assertIn(MANAGER, targeted)

		for call in ours:
			self.assertEqual(call.kwargs["message"]["invoice_name"], "TEST-QUEUE-ALERT-001")
			self.assertTrue(call.kwargs["after_commit"])

	def test_recipients_are_user_ids_not_email_addresses(self):
		"""publish_realtime targets User.name; the two are not guaranteed to match."""
		alias = "queue_alert_alias@example.com"
		_ensure_user(alias, [])
		frappe.db.set_value("User", alias, "email", "different-address@example.com")
		self.addCleanup(frappe.db.set_value, "User", alias, "email", alias)
		frappe.clear_cache()

		user_ids = _get_queue_failure_user_ids(alias)

		self.assertIn(alias, user_ids)
		self.assertNotIn("different-address@example.com", user_ids)

	def test_a_publish_failure_does_not_stop_the_record_keeping(self):
		"""The alert channel must never take down the durable record behind it."""
		invoice = SimpleNamespace(name="TEST-QUEUE-ALERT-002", customer="ACME", customer_name="ACME Ltd")
		subject = f"POS invoice queue failed: {invoice.name}"
		before = frappe.db.count("Notification Log", {"subject": subject})

		with (
			patch.object(sales_invoice.frappe, "publish_realtime", side_effect=Exception("socket down")),
			patch.object(sales_invoice, "_get_queue_failure_recipients", return_value=[]),
			patch.object(sales_invoice.frappe, "sendmail"),
			patch.object(sales_invoice.frappe, "log_error"),
		):
			_notify_queue_failure(invoice, CASHIER, "boom")

		after = frappe.db.count("Notification Log", {"subject": subject})
		self.assertEqual(after, before + 1, "the Notification Log must still be written")

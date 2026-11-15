"""The till's side of cecypo_powerpack's price-list floor.

Checkout pre-validation lists below-minimum lines; checkout without a supervisor's
authorisation is refused before anything persists; a supervisor's PIN at the till gives a
token that lets the queued sale through and stamps who authorised it.

Skipped when cecypo_powerpack's floor is not installed: klik_pos must not need it.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from klik_pos.api.sales_invoice import (
	process_queued_sales_invoice,
	queue_sales_invoice,
	validate_checkout_invoice,
)
from klik_pos.api.sales_order import checkout_held_order, create_held_order
from klik_pos.testing import foreign_sales_order_workflows_off_for
from klik_pos.tests.test_opening_conflict import _profile, _shift
from klik_pos.tests.test_price_list_recording import CASHIER, CUSTOMER, ELITE, ITEM, RETAIL, _ensure_fixtures

ROLE = "_KPL Supervisor"
SUPERVISOR = "klik-price-floor-supervisor@example.com"
PIN = "2468"
ENFORCE_FIELD = "powerpack_enforce_list_floor"
SETTINGS = ("enable_min_selling_price", "min_selling_price_override_role", "min_selling_price_default_percent")


class TestPriceFloorCheckout(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.get_meta("POS Profile").has_field(ENFORCE_FIELD):
			raise unittest.SkipTest("cecypo_powerpack price-list floor is not installed")
		_ensure_fixtures()
		if not frappe.db.exists("Role", ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
		if not frappe.db.exists("User", SUPERVISOR):
			frappe.get_doc(
				{"doctype": "User", "email": SUPERVISOR, "first_name": "KPL Supervisor", "send_welcome_email": 0,
				 "roles": [{"role": ROLE}]}
			).insert(ignore_permissions=True)
		supervisor = frappe.get_doc("User", SUPERVISOR)
		supervisor.powerpack_pos_supervisor_pin = PIN
		supervisor.save(ignore_permissions=True)
		cls.till = _profile()
		frappe.db.set_value(
			"POS Profile",
			cls.till,
			{"selling_price_list": RETAIL, "allow_partial_payment": 1, "custom_allow_credit_sales_as_pos": 1, ENFORCE_FIELD: 1},
		)
		cls.shift = _shift(cls.till, CASHIER)
		cls.saved = {f: frappe.db.get_single_value("PowerPack Settings", f) for f in SETTINGS}
		foreign_sales_order_workflows_off_for(cls)
		frappe.db.set_single_value(
			"PowerPack Settings",
			{"enable_min_selling_price": 1, "min_selling_price_override_role": ROLE, "min_selling_price_default_percent": 0},
		)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.set_single_value("PowerPack Settings", cls.saved)
		if frappe.db.exists("POS Opening Entry", cls.shift):
			frappe.db.set_value("POS Opening Entry", cls.shift, "docstatus", 2)
			frappe.delete_doc("POS Opening Entry", cls.shift, force=True, ignore_permissions=True)
		for name in frappe.get_all("Sales Invoice", {"pos_profile": cls.till}, pluck="name"):
			for child in ("Sales Invoice Item", "Sales Invoice Payment", "Sales Taxes and Charges"):
				frappe.db.delete(child, {"parent": name})
			frappe.db.delete("GL Entry", {"voucher_no": name})
			frappe.db.delete("Payment Ledger Entry", {"voucher_no": name})
			frappe.db.delete("Sales Invoice", name)
		frappe.delete_doc("POS Profile", cls.till, force=True, ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_cache(doctype="PowerPack Settings")
		super().tearDownClass()

	def setUp(self):
		frappe.clear_cache(doctype="PowerPack Settings")
		frappe.cache.delete_value(f"powerpack_pin_fail:{SUPERVISOR}")
		frappe.flags.powerpack_test_min_selling_price = True
		frappe.set_user(CASHIER)

	def tearDown(self):
		frappe.flags.powerpack_test_min_selling_price = False
		frappe.set_user("Administrator")
		frappe.db.set_value("POS Profile", self.till, ENFORCE_FIELD, 1)
		frappe.db.rollback()

	def _payload(self, rate=420, header=RETAIL):
		return {
			"customer": {"id": CUSTOMER},
			"items": [{"id": ITEM, "item_code": ITEM, "quantity": 1, "price": rate, "uom": "Nos"}],
			"isCreditSale": True,
			"dueDate": add_days(nowdate(), 7),
			"checkout_request_id": frappe.generate_hash(length=24),
			"businessType": "B2C",
			"enable_background_invoice_submission": 1,
			"selling_price_list": header,
		}

	def test_preview_lists_below_floor_rows(self):
		result = validate_checkout_invoice(self._payload())
		self.assertTrue(result["success"], result.get("message"))
		self.assertTrue(result["tax_preview"]["price_floor"]["enforced"])
		(row,) = result["tax_preview"]["price_floor"]["rows"]
		self.assertEqual((row["idx"], row["item_code"], row["minimum"]), (1, ITEM, 450))

	def test_preview_on_cart_list_distributor_passes(self):
		result = validate_checkout_invoice(self._payload(header=ELITE))
		self.assertEqual(result["tax_preview"]["price_floor"]["rows"], [])

	def _line_pick(self, rate):
		"""The cart is on Retail (450); this line was switched to Elite (400) with the rate box's star."""
		return {**self._payload(rate=rate), "itemDiscounts": {ITEM: {"selectedPriceList": ELITE}}}

	def test_line_priced_from_a_lower_list_passes_at_that_lists_price(self):
		result = validate_checkout_invoice(self._line_pick(400))
		self.assertTrue(result["success"], result.get("message"))
		self.assertEqual(result["tax_preview"]["price_floor"]["rows"], [])

	def test_line_below_its_picked_list_is_judged_against_that_list(self):
		(row,) = validate_checkout_invoice(self._line_pick(390))["tax_preview"]["price_floor"]["rows"]
		self.assertEqual((row["minimum"], row["price_list"]), (400, ELITE))

	def test_checkout_of_a_line_at_its_picked_lists_price_needs_no_authorisation(self):
		result = queue_sales_invoice(self._line_pick(400))
		self.assertTrue(result["success"], result.get("message"))

	def test_held_line_at_its_picked_lists_price_is_not_sent_for_approval(self):
		if not frappe.get_meta("Sales Order").has_field("powerpack_price_breach"):
			self.skipTest("cecypo_powerpack price-approval fields are not installed")
		frappe.set_user("Administrator")
		settings = frappe.get_single("PowerPack Settings")
		settings.msp_approval_sales_order = 1
		settings.save()
		frappe.set_user(CASHIER)
		asked = create_held_order({**self._line_pick(400), "status": "held", "request_price_approval": True})
		self.assertTrue(asked["success"], asked.get("message"))
		self.assertFalse(asked["approval_requested"])

	def test_unticked_till_preview_not_enforced(self):
		frappe.db.set_value("POS Profile", self.till, ENFORCE_FIELD, 0)
		result = validate_checkout_invoice(self._payload())
		self.assertEqual(result["tax_preview"]["price_floor"], {"enforced": False, "rows": []})

	def test_checkout_refused_without_authorisation(self):
		before = frappe.db.count("Sales Invoice", {"pos_profile": self.till})
		result = queue_sales_invoice(self._payload())
		self.assertFalse(result["success"])
		self.assertIn("price should be at least", result["message"])
		self.assertEqual(frappe.db.count("Sales Invoice", {"pos_profile": self.till}), before)

	def test_wrong_pin_returns_not_ok(self):
		from klik_pos.api.price_floor import authorise

		self.assertFalse(authorise(self._payload(), SUPERVISOR, "0000")["ok"])

	def test_supervisors_listed(self):
		from klik_pos.api.price_floor import get_supervisors

		self.assertIn(SUPERVISOR, [s["name"] for s in get_supervisors()])

	def test_authorise_then_queue_stamps_and_worker_submits(self):
		from klik_pos.api.price_floor import authorise

		auth = authorise(self._payload(), SUPERVISOR, PIN)
		self.assertTrue(auth["ok"], auth.get("message"))
		payload = self._payload()
		payload["price_authorisation"] = auth["token"]
		result = queue_sales_invoice(payload)
		self.assertTrue(result["success"], result.get("message"))
		name = result["invoice_name"]
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "powerpack_price_approved_by"), SUPERVISOR)

		process_queued_sales_invoice(name, requested_by=CASHIER)
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "docstatus"), 1)

	def test_lowered_after_authorisation_refused(self):
		from klik_pos.api.price_floor import authorise

		token = authorise(self._payload(rate=420), SUPERVISOR, PIN)["token"]
		payload = self._payload(rate=410)
		payload["price_authorisation"] = token
		result = queue_sales_invoice(payload)
		self.assertFalse(result["success"])
		self.assertIn("price should be at least", result["message"])

	def test_ask_manager_holds_for_approval_and_the_approval_covers_checkout(self):
		from unittest.mock import patch

		from frappe.model.workflow import apply_workflow

		if not frappe.get_meta("Sales Order").has_field("powerpack_price_breach"):
			self.skipTest("cecypo_powerpack price-approval fields are not installed")
		emails = patch(
			"frappe.workflow.doctype.workflow_action.workflow_action.send_workflow_action_email",
			new=lambda doc, transitions: None,
		)
		emails.start()
		self.addCleanup(emails.stop)
		frappe.set_user("Administrator")
		settings = frappe.get_single("PowerPack Settings")
		settings.msp_approval_sales_order = 1
		settings.save()  # rolled back in tearDown, workflow and all
		frappe.set_user(CASHIER)

		parked = create_held_order({**self._payload(), "status": "held"})
		self.assertTrue(parked["success"], parked.get("message"))
		self.assertFalse(parked["approval_requested"])  # plain Hold: judged at checkout, by PIN

		asked = create_held_order({**self._payload(), "status": "held", "request_price_approval": True})
		self.assertTrue(asked["success"], asked.get("message"))
		self.assertTrue(asked["approval_requested"])
		order_id = asked["order_name"]
		self.assertEqual(frappe.db.get_value("Sales Order", order_id, "workflow_state"), "Price Approval Pending")

		frappe.set_user("Administrator")  # holds the override role
		apply_workflow(frappe.get_doc("Sales Order", order_id), "Approve")
		frappe.set_user(CASHIER)

		preview = validate_checkout_invoice({**self._payload(), "held_order_id": order_id})
		self.assertEqual(preview["tax_preview"]["price_floor"]["rows"], [])  # the till lets it through

		result = checkout_held_order(order_id, {**self._payload(), "enable_background_invoice_submission": 0})
		self.assertTrue(result["success"], result.get("message"))
		self.assertEqual(frappe.db.get_value("Sales Invoice", result["invoice_name"], "powerpack_source_order"), order_id)

"""klik_pos's side of cecypo_powerpack's Sales Order price approval.

With PowerPack's Price Approval on Sales Order active, holding a breaching cart requests
approval in the same call, the held order reports its approval state everywhere klik reads
one, its draft-to-draft workflow actions (Withdraw Approval) are reachable through klik's own
access check, and checkout stamps the invoice with the source order so an approval survives
the order being deleted. A checkout that still needs approval is refused before anything is
persisted.

Everything here is skipped outright when cecypo_powerpack's price-approval custom fields are
not on this site: klik_pos must not require the app to be installed.

Settings: unlike the plan's other price-approval suites, this class turns BOTH
``msp_approval_sales_order`` and ``msp_approval_sales_invoice`` on (not "sales order only,
others off"). With the Sales Invoice switch off, cecypo_powerpack's own hard block ("Net
selling rate should be at least ...") fires straight out of ``doc.insert()`` for any
breaching, uncovered invoice - before klik's own ``_refuse_unapproved_price_breach`` ever
runs - so a pending or unapproved checkout is refused with that wording, not "need approval".
Turning the Sales Invoice switch on lets cecypo_powerpack save the breaching draft (flagged,
uncovered) instead of throwing, which is what makes klik's own refusal the operative gate -
this is also exactly the configuration cecypo_powerpack's own TestRoutedBreach suite runs
under. See task-2-report.md for the probe that pinned this down.
"""

from unittest.mock import patch

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from klik_pos.api.sales_invoice import process_queued_sales_invoice, queue_sales_invoice
from klik_pos.api.sales_order import (
	apply_held_order_action,
	checkout_held_order,
	create_held_order,
	get_held_order_actions,
	get_held_order_details,
	get_held_orders,
)
from klik_pos.testing import foreign_sales_order_workflows_off_for
from klik_pos.tests.test_opening_conflict import _profile, _shift, _user

ROLE = "_Klik MSP Override Role"
ITEM = "_Klik MSP Approval Item"
CUSTOMER = "_Test Customer"
CASHIER = "klik-msp-approval-cashier@example.com"

STATE_DRAFT = "Draft"
STATE_PENDING = "Price Approval Pending"
STATE_APPROVED = "Price Approved"
ACTION_REQUEST = "Request Price Approval"
ACTION_APPROVE = "Approve"
ACTION_WITHDRAW = "Withdraw Approval"


def _ensure_item():
	"""A stock item with a fixed valuation rate of 100 (floor 104 at 4%), and
	allow_negative_stock so klik's own reservation/stock checks never fire for it -
	the point of this suite is the price-approval gate, not warehouse stock."""
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": ITEM,
				"item_name": ITEM,
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"allow_negative_stock": 1,
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value(
		"Item", ITEM, {"valuation_rate": 100, "last_purchase_rate": 100, "allow_negative_stock": 1}
	)


class SettingsSnapshot:
	"""Put the site's own PowerPack Settings values back when the class is done.

	Same pattern as cecypo_powerpack.tests.test_price_approval.SettingsSnapshot: FrappeTestCase
	rolls the database back once per class, and the next class's setUpClass commits whatever is
	still pending, so a test that saves the singleton would otherwise overwrite the site's real
	configuration.
	"""

	SETTINGS_FIELDS = (
		"enable_min_selling_price",
		"min_selling_price_default_basis",
		"min_selling_price_default_percent",
		"min_selling_price_override_role",
		"min_selling_price_whole_sale",
		"msp_approval_quotation",
		"msp_approval_sales_order",
		"msp_approval_sales_invoice",
		"msp_approval_delivery_note",
	)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		settings = frappe.get_single("PowerPack Settings")
		cls._settings_before = {field: settings.get(field) for field in cls.SETTINGS_FIELDS}
		foreign_sales_order_workflows_off_for(cls)

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()
		settings = frappe.get_single("PowerPack Settings")
		for field, value in cls._settings_before.items():
			settings.set(field, value)
		settings.flags.ignore_version = True
		settings.save()
		frappe.db.commit()
		super().tearDownClass()


class TestHeldOrderPriceApproval(SettingsSnapshot, FrappeTestCase):
	"""Everything this class or a test commits to the real database is cleaned up again:
	the POS Profile and its shift (created once for the whole class, not per test — see
	setUpClass) in tearDownClass, and a per-test committed record (a held order that must
	survive _abort_checkout's full rollback — see tests 8 and 11) via addCleanup. tearDownClass
	ends with a guard that fails loudly if a POS Profile or an open shift for the cashier
	outlived the class, so a future leak here breaks the build instead of piling up on dev.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.get_meta("Sales Order").has_field("powerpack_price_breach"):
			raise __import__("unittest").SkipTest("cecypo_powerpack price-approval fields are not installed")

		# Baseline for the leak guard in tearDownClass, taken before this class creates
		# anything of its own.
		cls._profiles_before = frappe.db.count("POS Profile", {"name": ["like", "_Test Opening Conflict %"]})
		cls._open_entries_before = frappe.db.count(
			"POS Opening Entry", {"user": CASHIER, "docstatus": 1, "status": "Open"}
		)

		from cecypo_powerpack import price_approval as pa
		from cecypo_powerpack.tests.test_price_approval import ensure_workflow_state_columns

		pa.setup_custom_fields()
		ensure_workflow_state_columns()

		if not frappe.db.exists("Role", ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()

		_ensure_item()

		# The cashier user is a persistent, reused fixture (like cecypo_powerpack's own
		# _MSP Item / _MSP Parent) and is deliberately never deleted. The POS Profile and its
		# shift are NOT: they are created once here, for the whole class, rather than once per
		# test (a run used to leave one committed profile and one open shift behind for every
		# single test method — see tearDownClass for the cleanup and the guard).
		_user(CASHIER)
		user_doc = frappe.get_doc("User", CASHIER)
		# Accounts User: ERPNext's get_pos_profile needs read on the till's POS Profile, as a real cashier has.
		missing = {"Sales User", "Accounts User"} - {row.role for row in user_doc.roles}
		if missing:
			user_doc.extend("roles", [{"role": role} for role in sorted(missing)])
			user_doc.flags.ignore_permissions = True
			user_doc.save()

		cls.till = _profile()
		# These tests sell on credit; the till must allow it.
		frappe.db.set_value("POS Profile", cls.till, {"allow_partial_payment": 1, "custom_allow_credit_sales_as_pos": 1})
		cls.shift = _shift(cls.till, CASHIER)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")

		shift = getattr(cls, "shift", None)
		if shift and frappe.db.exists("POS Opening Entry", shift):
			doc = frappe.get_doc("POS Opening Entry", shift)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("POS Opening Entry", shift, force=True, ignore_permissions=True)

		till = getattr(cls, "till", None)
		if till and frappe.db.exists("POS Profile", till):
			frappe.delete_doc("POS Profile", till, force=True, ignore_permissions=True)

		frappe.db.commit()

		# The leak guard: nothing this class committed may still be here.
		profiles_after = frappe.db.count("POS Profile", {"name": ["like", "_Test Opening Conflict %"]})
		open_entries_after = frappe.db.count(
			"POS Opening Entry", {"user": CASHIER, "docstatus": 1, "status": "Open"}
		)
		if profiles_after != cls._profiles_before or open_entries_after != cls._open_entries_before:
			raise AssertionError(
				"TestHeldOrderPriceApproval leaked committed data: "
				f"POS Profile count {cls._profiles_before} -> {profiles_after}, "
				f"open POS Opening Entry count for {CASHIER} "
				f"{cls._open_entries_before} -> {open_entries_after}"
			)

		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Selling Settings", "validate_selling_price", 0)
		frappe.flags.powerpack_test_min_selling_price = True

		# Frappe sends the workflow action email inline under frappe.in_test, and hands
		# attach_print the very doc object the test holds - see TestRoutedBreach.setUp in
		# cecypo_powerpack for the full explanation. The emails are frappe's, not ours.
		emails = patch(
			"frappe.workflow.doctype.workflow_action.workflow_action.send_workflow_action_email",
			new=lambda doc, transitions: None,
		)
		emails.start()
		self.addCleanup(emails.stop)

		self._configure(msp_approval_sales_order=1, msp_approval_sales_invoice=1)

		frappe.set_user(CASHIER)

	def tearDown(self):
		frappe.flags.powerpack_test_min_selling_price = False
		frappe.set_user("Administrator")
		# Submitting a Sales Order creates a Bin for our item, whose valuation_rate would
		# otherwise silently take the floor out of play for every test after the first
		# submit — see the identical comment in cecypo_powerpack's TestRoutedBreach.tearDown.
		# The POS Profile and shift created once in setUpClass are untouched by this: they
		# were committed there, not in this test, so this rollback cannot reach them.
		frappe.db.rollback()
		from cecypo_powerpack import price_approval as pa

		for dt in pa.APPROVAL_DOCTYPES:
			frappe.clear_cache(doctype=dt)
		frappe.clear_cache(doctype="PowerPack Settings")

	def _configure(self, **flags):
		s = frappe.get_single("PowerPack Settings")
		s.enable_min_selling_price = 1
		s.min_selling_price_default_basis = "Valuation Rate"
		s.min_selling_price_default_percent = 4
		s.min_selling_price_whole_sale = 0
		s.min_selling_price_override_role = ROLE
		s.set("min_selling_price_rules", [])
		for f in (
			"msp_approval_quotation",
			"msp_approval_sales_order",
			"msp_approval_sales_invoice",
			"msp_approval_delivery_note",
		):
			s.set(f, flags.get(f, 0))
		s.save()
		frappe.clear_cache(doctype="PowerPack Settings")
		return s

	# ---- helpers ----------------------------------------------------------------------

	def _hold(self, rate=90, qty=1, held_order_id=None):
		payload = {
			"customer": {"id": CUSTOMER},
			"items": [{"id": ITEM, "item_code": ITEM, "quantity": qty, "price": rate, "uom": "Nos"}],
			"status": "held",
		}
		if held_order_id:
			payload["held_order_id"] = held_order_id
		return create_held_order(payload)

	def _approve(self, order_id):
		"""Approve a pending held order as Administrator, who holds every role."""
		frappe.set_user("Administrator")
		so = frappe.get_doc("Sales Order", order_id)
		apply_workflow(so, ACTION_APPROVE)
		frappe.set_user(CASHIER)

	def _checkout_payload(self, rate=90, qty=1, background=False, request_id=None, extra=None):
		payload = {
			"customer": {"id": CUSTOMER},
			"items": [{"id": ITEM, "item_code": ITEM, "quantity": qty, "price": rate, "uom": "Nos"}],
			"isCreditSale": True,
			"dueDate": add_days(nowdate(), 7),
			"checkout_request_id": request_id or frappe.generate_hash(length=24),
			"businessType": "B2C",
			"enable_background_invoice_submission": 1 if background else 0,
		}
		if extra:
			payload.update(extra)
		return payload

	# ---- 1: holding a breaching cart auto-requests approval ---------------------------

	def test_holding_a_breaching_cart_requests_approval(self):
		result = self._hold(rate=90)
		self.assertTrue(result["success"], result.get("message"))
		self.assertTrue(result["approval_requested"])
		self.assertEqual(result["approval_state"], STATE_PENDING)
		self.assertEqual(result["price_breach"], 1)
		self.assertEqual(
			frappe.db.get_value("Sales Order", result["order_name"], "workflow_state"), STATE_PENDING
		)

	# ---- 2: a clean cart is not routed -------------------------------------------------

	def test_holding_a_clean_cart_does_not_request_approval(self):
		result = self._hold(rate=120)
		self.assertTrue(result["success"], result.get("message"))
		self.assertFalse(result["approval_requested"])
		self.assertEqual(result["price_breach"], 0)

	# ---- 3: listing and details both carry the approval fields ------------------------

	def test_listing_and_details_carry_approval_fields(self):
		held = self._hold(rate=90)
		order_id = held["order_name"]

		rows = get_held_orders(skip_opening_entry_filter=True, limit=100)["data"]
		row = next(r for r in rows if r["name"] == order_id)
		self.assertEqual(row["approval_state"], STATE_PENDING)
		self.assertEqual(row["price_breach"], 1)

		details = get_held_order_details(order_id)
		self.assertTrue(details["success"], details.get("message"))
		self.assertEqual(details["approval_state"], STATE_PENDING)
		self.assertEqual(details["price_breach"], 1)

	# ---- 4: withdraw after an override user approves -----------------------------------

	def test_withdraw_approval_after_override_user_approves(self):
		held = self._hold(rate=90)
		order_id = held["order_name"]
		self._approve(order_id)

		actions = get_held_order_actions(order_id)
		self.assertTrue(actions["success"], actions.get("message"))
		self.assertIn(ACTION_WITHDRAW, actions["actions"])
		self.assertEqual(actions["approval_state"], STATE_APPROVED)

		result = apply_held_order_action(order_id, ACTION_WITHDRAW)
		self.assertTrue(result["success"], result.get("message"))
		self.assertEqual(result["approval_state"], STATE_DRAFT)
		self.assertEqual(frappe.db.get_value("Sales Order", order_id, "workflow_state"), STATE_DRAFT)

	# ---- 4b: a manager approves or rejects from the till -------------------------------
	# Which held orders a till user may see is _may_act_on_held_order's job, tested in
	# test_held_order_access_rule; here the approver is taken to be at a till that sees them.

	def _as_approver(self):
		frappe.set_user("Administrator")
		seen = patch("klik_pos.api.sales_order._may_act_on_held_order", return_value=True)
		seen.start()
		self.addCleanup(seen.stop)

	def test_listing_offers_approve_and_reject_only_to_an_approver(self):
		order_id = self._hold(rate=90)["order_name"]

		row = next(r for r in get_held_orders(skip_opening_entry_filter=True, limit=100)["data"] if r["name"] == order_id)
		self.assertEqual(row["approval_actions"], [])  # the cashier who asked may not approve

		from klik_pos.api.sales_order import _my_approval_actions

		self.assertEqual(_my_approval_actions(order_id), [])
		frappe.set_user("Administrator")
		self.assertEqual(sorted(_my_approval_actions(order_id)), ["Approve", "Reject"])

	def test_approver_approves_from_the_till(self):
		order_id = self._hold(rate=90)["order_name"]
		self._as_approver()
		result = apply_held_order_action(order_id, ACTION_APPROVE)
		self.assertTrue(result["success"], result.get("message"))
		self.assertEqual(result["approval_state"], STATE_APPROVED)

	def test_approver_rejects_from_the_till(self):
		order_id = self._hold(rate=90)["order_name"]
		self._as_approver()
		result = apply_held_order_action(order_id, "Reject")
		self.assertTrue(result["success"], result.get("message"))
		self.assertEqual(result["approval_state"], STATE_DRAFT)

	# ---- 5: an action not on offer fails cleanly ---------------------------------------

	def test_applying_an_unavailable_action_fails(self):
		held = self._hold(rate=90)
		order_id = held["order_name"]

		result = apply_held_order_action(order_id, ACTION_APPROVE)
		self.assertFalse(result["success"])

	# ---- 6: checkout of an approved held order, direct mode ----------------------------

	def test_checkout_of_an_approved_held_order_direct(self):
		held = self._hold(rate=90)
		order_id = held["order_name"]
		self._approve(order_id)
		approved_rows = frappe.db.get_value("Sales Order", order_id, "powerpack_price_approved_rows")
		self.assertTrue(approved_rows)

		result = checkout_held_order(order_id, self._checkout_payload(rate=90))
		self.assertTrue(result["success"], result.get("message"))

		invoice = frappe.db.get_value(
			"Sales Invoice",
			result["invoice_name"],
			["powerpack_source_order", "powerpack_price_approved_rows", "docstatus"],
			as_dict=True,
		)
		self.assertEqual(invoice.powerpack_source_order, order_id)
		self.assertEqual(invoice.powerpack_price_approved_rows, approved_rows)
		self.assertEqual(invoice.docstatus, 1)
		self.assertFalse(frappe.db.exists("Sales Order", order_id))

	# ---- 7: checkout of an approved held order, background mode ------------------------

	def test_checkout_of_an_approved_held_order_background(self):
		held = self._hold(rate=90)
		order_id = held["order_name"]
		self._approve(order_id)
		approved_rows = frappe.db.get_value("Sales Order", order_id, "powerpack_price_approved_rows")

		result = checkout_held_order(order_id, self._checkout_payload(rate=90, background=True))
		self.assertTrue(result["success"], result.get("message"))
		invoice_name = result["invoice_name"]

		queued = frappe.db.get_value(
			"Sales Invoice",
			invoice_name,
			["powerpack_source_order", "powerpack_price_approved_rows", "docstatus"],
			as_dict=True,
		)
		self.assertEqual(queued.powerpack_source_order, order_id)
		self.assertEqual(queued.powerpack_price_approved_rows, approved_rows)
		self.assertEqual(queued.docstatus, 0)
		self.assertFalse(frappe.db.exists("Sales Order", order_id))

		frappe.set_user("Administrator")
		submit_result = process_queued_sales_invoice(invoice_name)
		self.assertTrue(submit_result["success"], submit_result.get("message"))
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice_name, "docstatus"), 1)

	# ---- 8: checkout of a still-pending held order is refused ---------------------------

	def test_checkout_of_a_pending_held_order_is_refused(self):
		held = self._hold(rate=90)
		order_id = held["order_name"]
		# _abort_checkout's rollback (the naming-counter burn guard - see
		# api/sales_invoice.py) undoes the WHOLE transaction, not just the invoice, so an
		# uncommitted hold from earlier in this same test would vanish along with the
		# failed checkout. Commit it first, as a real request boundary would, and clean it
		# up explicitly since tearDown's rollback can no longer reach it either.
		frappe.db.commit()
		self.addCleanup(self._delete_committed_order, order_id)
		before = frappe.db.count("Sales Invoice")

		result = checkout_held_order(order_id, self._checkout_payload(rate=90))

		self.assertFalse(result["success"])
		self.assertIn("need approval", result["message"])
		self.assertEqual(frappe.db.count("Sales Invoice"), before)
		# The still-pending order was never touched by the failed checkout.
		self.assertTrue(frappe.db.exists("Sales Order", order_id))

	def _delete_committed_order(self, order_id):
		frappe.set_user("Administrator")
		if frappe.db.exists("Sales Order", order_id):
			frappe.delete_doc("Sales Order", order_id, force=True, ignore_permissions=True)
			frappe.db.commit()

	# ---- 9: a direct (non-held) breaching checkout is refused, and cannot be spoofed ----

	def test_direct_checkout_of_a_breaching_cart_is_refused_and_not_spoofable(self):
		before = frappe.db.count("Sales Invoice")
		payload = self._checkout_payload(
			rate=90,
			extra={"source_order": "SAL-ORD-DOES-NOT-EXIST", "powerpack_source_order": "SAL-ORD-DOES-NOT-EXIST"},
		)

		result = queue_sales_invoice(payload)

		self.assertFalse(result["success"])
		self.assertIn("need approval", result["message"])
		self.assertEqual(frappe.db.count("Sales Invoice"), before)

	# ---- 10: with the Sales Order switch off, the floor still hard-blocks ---------------

	def test_holding_is_still_hard_blocked_with_the_workflow_off(self):
		frappe.set_user("Administrator")
		self._configure()  # every msp_approval_* flag off; the workflow deactivates
		frappe.set_user(CASHIER)

		result = self._hold(rate=90)

		self.assertFalse(result["success"])
		self.assertIn("at least", result["message"])

	# ---- 11: the recommended till setup - Sales Order routed, Sales Invoice not ---------

	# Split into two independent test methods on purpose, not one with two parts: part (b)
	# needs an explicit frappe.db.commit() to survive _abort_checkout's full rollback (see
	# test 8), and frappe.db.commit() finalises the WHOLE current transaction, not just the
	# row it is meant for. In one shared test method, part (a)'s still-uncommitted invoice
	# would be permanently committed by part (b)'s commit too — which is exactly what
	# happened during Part B of the leak fix (see task-2-report.md's fix-round-2 section).
	# Two test methods means each gets its own setUp/tearDown, so tearDown's rollback clears
	# part (a) before part (b)'s test ever runs.

	def test_recommended_setup_approved_checkout_still_works(self):
		"""SO routing on, SI routing off is the configuration Task 4 recommends for dev: an
		approved held order still covers its checkout invoice (carry_over_source_approval)."""
		frappe.set_user("Administrator")
		self._configure(msp_approval_sales_order=1, msp_approval_sales_invoice=0)
		frappe.set_user(CASHIER)
		try:
			held = self._hold(rate=90)
			order_id = held["order_name"]
			self._approve(order_id)
			approved_rows = frappe.db.get_value("Sales Order", order_id, "powerpack_price_approved_rows")
			self.assertTrue(approved_rows)

			result = checkout_held_order(order_id, self._checkout_payload(rate=90))
			self.assertTrue(result["success"], result.get("message"))
			invoice = frappe.db.get_value(
				"Sales Invoice",
				result["invoice_name"],
				["powerpack_source_order", "powerpack_price_approved_rows", "docstatus"],
				as_dict=True,
			)
			self.assertEqual(invoice.powerpack_source_order, order_id)
			self.assertEqual(invoice.powerpack_price_approved_rows, approved_rows)
			self.assertEqual(invoice.docstatus, 1)
		finally:
			frappe.set_user("Administrator")
			self._configure(msp_approval_sales_order=1, msp_approval_sales_invoice=1)
			frappe.set_user(CASHIER)

	def test_recommended_setup_pending_checkout_still_refused(self):
		"""Same setup as above: an unapproved held order still hits cecypo_powerpack's own
		floor message directly, since with invoice routing off there is no draft-save window
		for klik's own _refuse_unapproved_price_breach to be the operative gate - see the
		module docstring."""
		frappe.set_user("Administrator")
		self._configure(msp_approval_sales_order=1, msp_approval_sales_invoice=0)
		frappe.set_user(CASHIER)
		try:
			pending = self._hold(rate=90)
			pending_order_id = pending["order_name"]
			# _abort_checkout's rollback undoes the whole transaction (see test 8) - commit
			# the hold first, as a real request boundary would, and clean it up explicitly.
			frappe.db.commit()
			self.addCleanup(self._delete_committed_order, pending_order_id)
			before = frappe.db.count("Sales Invoice")

			result = checkout_held_order(pending_order_id, self._checkout_payload(rate=90))

			self.assertFalse(result["success"])
			self.assertTrue(
				"at least" in result["message"] or "need approval" in result["message"],
				result["message"],
			)
			self.assertEqual(frappe.db.count("Sales Invoice"), before)
		finally:
			frappe.set_user("Administrator")
			self._configure(msp_approval_sales_order=1, msp_approval_sales_invoice=1)
			frappe.set_user(CASHIER)

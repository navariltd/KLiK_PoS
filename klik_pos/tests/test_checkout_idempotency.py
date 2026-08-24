"""A checkout sent twice must produce one invoice, not two.

queue_sales_invoice built a brand-new Sales Invoice on every call. Nothing keyed the
request, so a double-click, a mid-flight refresh, a retried fetch or a proxy that
timed out after the server had already committed each left the customer with a second
order. The SPA's isProcessingPayment flag was the only guard and it dies with the tab.

The fix keys each checkout on a browser-generated request id recorded in a
Klik Checkout Request ledger. The claim happens before any document work, so a replay
returns what the first call produced and creates nothing. A client that lost its HTTP
response can read the outcome back through get_checkout_request_status instead of
resubmitting.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_invoice import (
	CHECKOUT_REQUEST_DOCTYPE,
	_claim_checkout_request,
	_normalize_checkout_request_id,
	_update_checkout_request,
	get_checkout_request_status,
	queue_sales_invoice,
)

ITEM_GROUP = "TEST-CHECKOUT-IDEM-GROUP"
ITEM_CODE = "TEST-CHECKOUT-IDEM-ITEM"
OTHER_USER = "checkout_idem_other@example.com"


def _delete_requests(*request_ids):
	for request_id in request_ids:
		if request_id and frappe.db.exists(CHECKOUT_REQUEST_DOCTYPE, request_id):
			frappe.delete_doc(CHECKOUT_REQUEST_DOCTYPE, request_id, force=True, ignore_permissions=True)


class TestCheckoutRequestLedger(FrappeTestCase):
	"""The claim primitive: one key, one winner."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.request_id = frappe.generate_hash(length=24)
		self.addCleanup(_delete_requests, self.request_id)

	def test_a_fresh_key_is_claimed_and_leaves_a_processing_row(self):
		self.assertIsNone(_claim_checkout_request(self.request_id))

		row = frappe.db.get_value(
			CHECKOUT_REQUEST_DOCTYPE,
			self.request_id,
			["status", "requested_by", "sales_invoice"],
			as_dict=True,
		)
		self.assertEqual(row.status, "Processing")
		self.assertEqual(row.requested_by, "Administrator")
		self.assertFalse(row.sales_invoice)

	def test_claiming_the_same_key_twice_returns_the_first_row(self):
		self.assertIsNone(_claim_checkout_request(self.request_id))

		replay = _claim_checkout_request(self.request_id)
		self.assertIsNotNone(replay)
		self.assertEqual(replay.name, self.request_id)
		self.assertEqual(
			frappe.db.count(CHECKOUT_REQUEST_DOCTYPE, {"name": self.request_id}),
			1,
			"a replay must not add a second ledger row",
		)

	def test_no_request_id_means_no_ledger_row(self):
		before = frappe.db.count(CHECKOUT_REQUEST_DOCTYPE)
		self.assertIsNone(_claim_checkout_request(None))
		self.assertIsNone(_claim_checkout_request(""))
		self.assertEqual(frappe.db.count(CHECKOUT_REQUEST_DOCTYPE), before)

	def test_an_over_long_key_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			_normalize_checkout_request_id("x" * 141)

	def test_a_key_that_naming_would_not_preserve_is_refused(self):
		# A key that came back from naming altered would never match its own replay.
		for bad in ("has space", "angle<bracket>", "slash/key", "percent%key"):
			with self.subTest(key=bad), self.assertRaises(frappe.ValidationError):
				_normalize_checkout_request_id(bad)

	def test_a_uuid_key_is_accepted(self):
		uuid_key = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
		self.assertEqual(_normalize_checkout_request_id(uuid_key), uuid_key)

	def test_a_key_is_trimmed_and_blank_reads_as_absent(self):
		self.assertEqual(_normalize_checkout_request_id("  abc  "), "abc")
		self.assertIsNone(_normalize_checkout_request_id("   "))
		self.assertIsNone(_normalize_checkout_request_id(None))

	def test_a_key_belonging_to_another_user_is_refused(self):
		# Claimed by Administrator, then replayed by somebody else — a guessed or copied key
		# must not hand one cashier another's sale.
		_claim_checkout_request(self.request_id)

		if not frappe.db.exists("User", OTHER_USER):
			user = frappe.new_doc("User")
			user.email = OTHER_USER
			user.first_name = "Checkout Idem Other"
			user.insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "User", OTHER_USER, force=True, ignore_permissions=True)

		# A System Manager may inspect anyone's key; the cashier next to them may not.
		frappe.set_user(OTHER_USER)
		self.addCleanup(frappe.set_user, "Administrator")
		try:
			with self.assertRaises(frappe.PermissionError):
				_claim_checkout_request(self.request_id)
		finally:
			frappe.set_user("Administrator")


class TestCheckoutRequestStatus(FrappeTestCase):
	"""What a client asks after it loses the response."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.request_id = frappe.generate_hash(length=24)
		self.addCleanup(_delete_requests, self.request_id)

	def test_an_unknown_key_reports_not_found(self):
		response = get_checkout_request_status(frappe.generate_hash(length=24))
		self.assertTrue(response["success"])
		self.assertEqual(response["checkout_status"], "not_found")

	def test_a_blank_key_reports_not_found(self):
		response = get_checkout_request_status("")
		self.assertTrue(response["success"])
		self.assertEqual(response["checkout_status"], "not_found")

	def test_a_claimed_but_unfinished_key_reports_processing(self):
		_claim_checkout_request(self.request_id)

		response = get_checkout_request_status(self.request_id)
		self.assertTrue(response["success"], "the lookup succeeded even if the checkout has not")
		self.assertEqual(response["checkout_status"], "processing")
		self.assertTrue(response["idempotent_replay"])

	def test_a_recorded_invoice_is_reported_back(self):
		invoice = frappe.get_all(
			"Sales Invoice", filters={"docstatus": 1, "is_return": 0}, pluck="name", limit=1
		)
		if not invoice:
			self.skipTest("no posted Sales Invoice on this site to point the ledger at")

		_claim_checkout_request(self.request_id)
		_update_checkout_request(self.request_id, status="Accepted", invoice_name=invoice[0])

		response = get_checkout_request_status(self.request_id)
		self.assertTrue(response["success"])
		self.assertEqual(response["invoice_name"], invoice[0])
		self.assertEqual(response["checkout_status"], "submitted")

	def test_a_failed_checkout_reports_its_error(self):
		_claim_checkout_request(self.request_id)
		_update_checkout_request(self.request_id, status="Failed", error_message="stock ran out")

		response = get_checkout_request_status(self.request_id)
		self.assertTrue(response["success"], "the lookup succeeded; the checkout did not")
		self.assertEqual(response["checkout_status"], "failed")
		self.assertIn("stock ran out", response["message"])


class TestCheckoutReplayCreatesNothing(FrappeTestCase):
	"""The claim must come before any document work."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.request_id = frappe.generate_hash(length=24)
		self.addCleanup(_delete_requests, self.request_id)

	def test_a_replay_short_circuits_before_the_payload_is_even_parsed(self):
		invoice = frappe.get_all(
			"Sales Invoice", filters={"docstatus": 1, "is_return": 0}, pluck="name", limit=1
		)
		if not invoice:
			self.skipTest("no posted Sales Invoice on this site to point the ledger at")

		_claim_checkout_request(self.request_id)
		_update_checkout_request(self.request_id, status="Accepted", invoice_name=invoice[0])

		invoices_before = frappe.db.count("Sales Invoice")
		# A payload with no customer and no items: anything that reaches parse_invoice_data
		# throws. Reaching the recorded invoice instead proves the claim runs first.
		response = queue_sales_invoice({"checkout_request_id": self.request_id})

		self.assertTrue(response["success"])
		self.assertTrue(response["idempotent_replay"])
		self.assertEqual(response["invoice_name"], invoice[0])
		self.assertEqual(
			frappe.db.count("Sales Invoice"),
			invoices_before,
			"a replay must not create a second invoice",
		)

	def test_a_failure_that_created_an_invoice_is_replayed_not_retried(self):
		invoice = frappe.get_all(
			"Sales Invoice", filters={"docstatus": 1, "is_return": 0}, pluck="name", limit=1
		)
		if not invoice:
			self.skipTest("no posted Sales Invoice on this site to point the ledger at")

		_claim_checkout_request(self.request_id)
		_update_checkout_request(
			self.request_id, status="Failed", invoice_name=invoice[0], error_message="submit blew up"
		)

		invoices_before = frappe.db.count("Sales Invoice")
		response = queue_sales_invoice({"checkout_request_id": self.request_id})

		self.assertFalse(response["success"])
		self.assertTrue(response["idempotent_replay"])
		self.assertEqual(response["invoice_name"], invoice[0])
		self.assertIn("submit blew up", response["message"])
		self.assertEqual(frappe.db.count("Sales Invoice"), invoices_before)

	def test_a_failure_that_created_nothing_lets_the_same_key_try_again(self):
		# Nothing exists to be duplicated, so blocking the retry would only strand the cart.
		_claim_checkout_request(self.request_id)
		_update_checkout_request(self.request_id, status="Failed", error_message="card declined")

		invoices_before = frappe.db.count("Sales Invoice")
		response = queue_sales_invoice({"checkout_request_id": self.request_id})

		self.assertFalse(response["success"], "the empty payload still fails on its own merits")
		self.assertNotIn(
			"idempotent_replay",
			response,
			"the key was reopened, so this is a fresh attempt rather than a replay",
		)
		self.assertNotIn("card declined", response["message"], "the stale failure must not be replayed")
		self.assertEqual(frappe.db.count("Sales Invoice"), invoices_before)


class TestHeldOrderCheckoutReplay(FrappeTestCase):
	"""A held order is deleted once its invoice exists, so the replay must not need it."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.request_id = frappe.generate_hash(length=24)
		self.addCleanup(_delete_requests, self.request_id)

	def test_a_replay_answers_after_the_sales_order_is_gone(self):
		invoice = frappe.get_all(
			"Sales Invoice", filters={"docstatus": 1, "is_return": 0}, pluck="name", limit=1
		)
		if not invoice:
			self.skipTest("no posted Sales Invoice on this site to point the ledger at")

		from klik_pos.api.sales_order import checkout_held_order

		_claim_checkout_request(self.request_id)
		_update_checkout_request(self.request_id, status="Accepted", invoice_name=invoice[0])

		invoices_before = frappe.db.count("Sales Invoice")
		# The order id no longer resolves — exactly the state a successful first call leaves.
		response = checkout_held_order(
			"SO-KLIK-CHECKOUT-IDEM-GONE", {"checkout_request_id": self.request_id}
		)

		self.assertTrue(response["success"], response.get("message"))
		self.assertTrue(response["idempotent_replay"])
		self.assertEqual(response["invoice_name"], invoice[0])
		self.assertEqual(frappe.db.count("Sales Invoice"), invoices_before)


class TestCheckoutEndToEnd(FrappeTestCase):
	"""One cart, two identical requests, one invoice."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

		from klik_pos.api.sales_invoice import get_current_pos_opening_entry

		if not get_current_pos_opening_entry():
			cls.ready = False
			return

		from klik_pos.klik_pos.utils import get_current_pos_profile

		cls.pos_profile = get_current_pos_profile()
		cls.company = cls.pos_profile.company
		cls.warehouse = cls.pos_profile.warehouse or frappe.db.get_value(
			"Warehouse", {"is_group": 0, "company": cls.company}, "name"
		)
		cls.payment_mode = frappe.db.get_value(
			"POS Payment Method", {"parent": cls.pos_profile.name}, "mode_of_payment"
		)
		cls.customer = frappe.db.get_value(
			"Sales Invoice", {"docstatus": 1, "is_return": 0, "company": cls.company}, "customer"
		)
		if not (cls.warehouse and cls.payment_mode and cls.customer):
			cls.ready = False
			return

		if not frappe.db.exists("Item Group", ITEM_GROUP):
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": ITEM_GROUP,
					"parent_item_group": "All Item Groups",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Item", ITEM_CODE):
			item = frappe.new_doc("Item")
			item.item_code = ITEM_CODE
			item.item_name = ITEM_CODE
			item.item_group = ITEM_GROUP
			item.stock_uom = "Nos"
			item.is_stock_item = 1
			item.is_sales_item = 1
			item.insert(ignore_permissions=True)

		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		cls.stock_entry = make_stock_entry(
			item_code=ITEM_CODE, target=cls.warehouse, qty=20, basic_rate=10, company=cls.company
		)
		frappe.db.commit()
		cls.ready = True

	@classmethod
	def tearDownClass(cls):
		if getattr(cls, "ready", False):
			# The item cannot go while any invoice still references it.
			for name in frappe.get_all(
				"Sales Invoice Item", filters={"item_code": ITEM_CODE}, pluck="parent", distinct=True
			):
				if not frappe.db.exists("Sales Invoice", name):
					continue
				invoice = frappe.get_doc("Sales Invoice", name)
				if invoice.docstatus == 1:
					invoice.flags.ignore_permissions = True
					invoice.cancel()
				frappe.delete_doc("Sales Invoice", name, force=True, ignore_permissions=True)

			if getattr(cls, "stock_entry", None) and frappe.db.exists("Stock Entry", cls.stock_entry.name):
				entry = frappe.get_doc("Stock Entry", cls.stock_entry.name)
				if entry.docstatus == 1:
					entry.flags.ignore_permissions = True
					entry.cancel()
				frappe.delete_doc("Stock Entry", cls.stock_entry.name, force=True, ignore_permissions=True)

			for name in frappe.get_all("Bin", filters={"item_code": ITEM_CODE}, pluck="name"):
				frappe.delete_doc("Bin", name, force=True, ignore_permissions=True)
			if frappe.db.exists("Item", ITEM_CODE):
				frappe.delete_doc("Item", ITEM_CODE, force=True, ignore_permissions=True)
			if frappe.db.exists("Item Group", ITEM_GROUP):
				frappe.delete_doc("Item Group", ITEM_GROUP, force=True, ignore_permissions=True)
			frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		if not getattr(self.__class__, "ready", False):
			self.skipTest("no open POS Opening Entry / payment mode / customer on this site")
		frappe.set_user("Administrator")
		self.request_id = frappe.generate_hash(length=24)
		self.addCleanup(_delete_requests, self.request_id)

	def _payload(self):
		return {
			"checkout_request_id": self.request_id,
			"customer": {"id": self.customer},
			"items": [
				{
					"id": ITEM_CODE,
					"item_code": ITEM_CODE,
					"quantity": 1,
					"price": 100,
					"uom": "Nos",
				}
			],
			"amountPaid": 100,
			"paymentMethods": [{"method": self.payment_mode, "amount": 100}],
			"businessType": "B2C",
		}

	def test_the_same_request_id_twice_creates_exactly_one_invoice(self):
		invoices_before = frappe.db.count("Sales Invoice")

		first = queue_sales_invoice(self._payload())
		self.assertTrue(first["success"], first.get("message"))
		self.assertEqual(first["checkout_request_id"], self.request_id)
		created = first["invoice_name"]
		self.addCleanup(self._remove_invoice, created)

		second = queue_sales_invoice(self._payload())

		self.assertTrue(second["success"], second.get("message"))
		self.assertTrue(second["idempotent_replay"])
		self.assertEqual(second["invoice_name"], created)
		self.assertEqual(
			frappe.db.count("Sales Invoice"),
			invoices_before + 1,
			"the retried checkout created a duplicate order",
		)

	def test_a_checkout_without_a_request_id_still_works_and_writes_no_ledger_row(self):
		ledger_before = frappe.db.count(CHECKOUT_REQUEST_DOCTYPE)

		payload = self._payload()
		payload.pop("checkout_request_id")
		response = queue_sales_invoice(payload)

		self.assertTrue(response["success"], response.get("message"))
		self.addCleanup(self._remove_invoice, response["invoice_name"])
		self.assertIsNone(response.get("checkout_request_id"))
		self.assertEqual(frappe.db.count(CHECKOUT_REQUEST_DOCTYPE), ledger_before)

	def _remove_invoice(self, name):
		if not name or not frappe.db.exists("Sales Invoice", name):
			return
		invoice = frappe.get_doc("Sales Invoice", name)
		if invoice.docstatus == 1:
			invoice.flags.ignore_permissions = True
			invoice.cancel()
		frappe.delete_doc("Sales Invoice", name, force=True, ignore_permissions=True)

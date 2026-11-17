import copy
import json
import random
import re
import time
from contextlib import contextmanager

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.stock.get_item_details import get_item_details
from frappe import _
from frappe.exceptions import ValidationError
from frappe.utils import cint, flt, fmt_money, getdate, nowdate, strip_html_tags

from klik_pos.api.customer_credit import apply_customer_credit, validate_allocations
from klik_pos.api.payment_rows import mode_label
from klik_pos.klik_pos.utils import error_reason, get_current_pos_profile, requested_warehouse
from klik_pos.overrides.loss_of_sale import PROFILE_FLAG as LOS_PROFILE_FLAG, split_cart_items

from .item.item_price import get_price_list_with_customer_priority, is_allowed_price_list
from .loyalty import (
	apply_loyalty_redemption,
	get_invoice_loyalty_summary,
	normalize_loyalty_redemption,
)
from .sql_builder import apply_sql_permissions

# Performance optimization: Cache frequently accessed data
_cached_company_data = {}
_cached_customer_data = {}
_cached_item_accounts = {}

QUEUE_STATUSES = {
	"queued": "Queued",
	"processing": "Processing",
	"failed": "Failed",
	"submitted": "Submitted",
}

CHECKOUT_REQUEST_DOCTYPE = "Klik Checkout Request"
CHECKOUT_REQUEST_ID_MAX_LENGTH = 140
CHECKOUT_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


def _normalize_checkout_request_id(value):
	"""The browser's idempotency key, or None when the client did not send one."""
	request_id = str(value or "").strip()
	if not request_id:
		return None
	# The key becomes the ledger row's name, so keep it to characters that survive naming
	# unchanged — a key that came back altered would never match its own replay.
	if len(request_id) > CHECKOUT_REQUEST_ID_MAX_LENGTH or not CHECKOUT_REQUEST_ID_PATTERN.match(request_id):
		frappe.throw(_("Checkout request ID is invalid."))
	return request_id


def _get_checkout_request(checkout_request_id, for_update=False):
	"""Read a checkout ledger row.

	Raw SQL rather than frappe.get_doc: the duplicate-insert path needs a read that sees
	the row the losing transaction just collided with, and FOR UPDATE to hold it while the
	replay response is built.
	"""
	if not checkout_request_id or not frappe.db.table_exists(CHECKOUT_REQUEST_DOCTYPE):
		return None

	lock_clause = " FOR UPDATE" if for_update else ""
	rows = frappe.db.sql(
		f"""
		SELECT name, requested_by, status, sales_invoice, error_message
		FROM `tab{CHECKOUT_REQUEST_DOCTYPE}`
		WHERE name = %s
		LIMIT 1{lock_clause}
		""",
		(checkout_request_id,),
		as_dict=True,
	)
	if not rows:
		return None

	request = rows[0]
	# A guessed or copied key must not hand one cashier another's sale.
	if request.requested_by != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(_("This checkout request belongs to another user."), frappe.PermissionError)
	return request


def _reclaim_failed_checkout_request(request):
	"""Reopen a key whose checkout failed without creating anything.

	The key exists to stop a second document, so a row that names no invoice has nothing
	to protect — refusing the retry would only strand the cart. Re-read under a row lock
	so two retries racing each other cannot both decide to proceed.
	"""
	if request.status != "Failed" or request.sales_invoice:
		return False

	locked = _get_checkout_request(request.name, for_update=True)
	if not locked or locked.status != "Failed" or locked.sales_invoice:
		return False

	frappe.db.set_value(
		CHECKOUT_REQUEST_DOCTYPE,
		request.name,
		{"status": "Processing", "error_message": None},
		update_modified=True,
	)
	return True


def _claim_checkout_request(checkout_request_id):
	"""Claim a checkout key. Returns the existing row on a replay, None on a fresh claim.

	The unique index on request_id is the lock: a concurrent duplicate blocks on the insert
	until the first transaction settles, then finds the committed row and replays.
	"""
	if not checkout_request_id:
		return None
	if not frappe.db.table_exists(CHECKOUT_REQUEST_DOCTYPE):
		frappe.throw(_("Checkout protection is not installed. Run the site migration before taking sales."))

	existing = _get_checkout_request(checkout_request_id)
	if existing:
		if _reclaim_failed_checkout_request(existing):
			return None
		return existing

	request = frappe.new_doc(CHECKOUT_REQUEST_DOCTYPE)
	request.request_id = checkout_request_id
	request.requested_by = frappe.session.user
	request.status = "Processing"
	# Savepoint so a collision rolls back only the failed insert, leaving the rest of the
	# checkout transaction usable for the replay read below.
	save_point = "klik_checkout_claim"
	frappe.db.savepoint(save_point)
	try:
		request.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		frappe.db.rollback(save_point=save_point)
		existing = _get_checkout_request(checkout_request_id, for_update=True)
		if existing:
			if _reclaim_failed_checkout_request(existing):
				return None
			return existing
		raise

	frappe.db.release_savepoint(save_point)
	return None


def _update_checkout_request(checkout_request_id, *, status, invoice_name=None, error_message=None):
	if not checkout_request_id or not frappe.db.exists(CHECKOUT_REQUEST_DOCTYPE, checkout_request_id):
		return
	values = {
		"status": status,
		"error_message": _truncate_queue_error(error_message) if error_message else None,
	}
	if invoice_name:
		values["sales_invoice"] = invoice_name
	frappe.db.set_value(CHECKOUT_REQUEST_DOCTYPE, checkout_request_id, values, update_modified=True)


# ---------------------------------------------------------------------------------------
# Checkout failure handling - the naming-counter burn guard.
#
# 2026-09-03: production lost 38 of 266 POS receipt numbers (CS-26-) in three days. Every
# missing number matched a Klik Checkout Request row with status=Failed and no invoice.
#
# The mechanism, and why the order below is load-bearing:
#
#   doc.insert() assigns the name FIRST - Document Naming Rule runs
#   `UPDATE tabDocument Naming Rule SET counter = counter + 1` inside set_new_name() - and
#   validates SECOND. So an ERPNext validation throw (a selling rate under valuation, say)
#   leaves the counter incremented and no invoice row. The old handler caught that
#   exception, recorded the failure and RETURNED normally, so nothing propagated and
#   Frappe's request handler committed the transaction. The increment survived, the invoice
#   never existed, and the receipt number was gone for good.
#
#   The desk (non-POS) path never had this bug: it lets the exception propagate, Frappe
#   rolls back, and the counter is restored. Only swallowing the exception breaks it.
#
# Two rules keep this fixed. Break either one and the burn returns silently:
#
#   1. _abort_checkout() is the ONLY way to fail a checkout. Do not add a bare
#      `return {"success": False, ...}` to an except block in this flow.
#   2. Inside _abort_checkout the rollback happens FIRST, before anything is recorded or
#      logged. Anything written before it - the Error Log row included - is discarded by it.
#
# A bare frappe.db.rollback() is NOT sufficient on its own: it also discards the claim row
# that _claim_checkout_request inserted, and _update_checkout_request early-returns when
# that row is gone. The failure record would silently no-op and the idempotency guard would
# stop working - turning skipped receipt numbers into duplicate invoices, which is worse.
# That is why _record_failed_checkout_request inserts as well as updates.
#
# Covered by klik_pos/tests/test_checkout_number_burn.py. If you rework checkout, keep
# those tests passing rather than rewriting them to match the new behaviour.
# ---------------------------------------------------------------------------------------

_SUBMIT_SAVEPOINT = "klik_checkout_submit"


class _CheckoutState:
	"""What _abort_checkout needs in order to undo a checkout correctly.

	The rollback turns on one fact: did the invoice reach the database before the failure?
	That fact lives here rather than in a loose local flag so it cannot drift away from the
	code that makes it true - mark_invoice_persisted() is both how the checkout is marked
	Accepted and how the fact is recorded, so a future persist point cannot set one without
	the other.
	"""

	def __init__(self):
		self.checkout_request_id = None
		self.claimed = False
		self.invoice_name = None
		self.savepoint = None

	def mark_claimed(self, checkout_request_id):
		self.checkout_request_id = checkout_request_id
		self.claimed = bool(checkout_request_id)

	def mark_invoice_persisted(self, invoice_name):
		"""The invoice is in the database now: mark the checkout Accepted and arm the savepoint.

		One call on purpose. The ledger must name the invoice before anything else can fail,
		or a retry would not find it; and from this point a failure must no longer roll the
		whole checkout back, because the draft is what Invoice History offers the cashier to
		retry. Both facts start being true at the same instant, so they are set together.
		"""
		self.invoice_name = invoice_name
		_update_checkout_request(self.checkout_request_id, status="Accepted", invoice_name=invoice_name)
		frappe.db.savepoint(_SUBMIT_SAVEPOINT)
		self.savepoint = _SUBMIT_SAVEPOINT


def _record_failed_checkout_request(state, error):
	"""Write the failure to the checkout ledger so a retry is told what happened.

	Inserts when the row is absent and updates when it is present, because the Class A
	rollback in _abort_checkout discards the claim row. Must run AFTER that rollback.
	"""
	if not state.claimed or not state.checkout_request_id:
		return

	if frappe.db.exists(CHECKOUT_REQUEST_DOCTYPE, state.checkout_request_id):
		_update_checkout_request(
			state.checkout_request_id,
			status="Failed",
			invoice_name=state.invoice_name,
			error_message=error,
		)
		return

	request = frappe.new_doc(CHECKOUT_REQUEST_DOCTYPE)
	request.request_id = state.checkout_request_id
	request.requested_by = frappe.session.user
	request.status = "Failed"
	request.error_message = _truncate_queue_error(error)
	if state.invoice_name:
		request.sales_invoice = state.invoice_name
	request.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------------------
# Lock races. A busy counter has several tills posting at once, and MySQL settles a lock race
# by killing one transaction (a deadlock) or giving up on a wait (a lock wait timeout). A
# checkout, a draft and the queue worker commit nothing before the end, so a full rollback
# leaves no trace - not even the receipt number - and the same work a moment later is safe.
#
# Their failure handlers report errors instead of raising, so each one first calls
# _raise_lock_race(e): while an attempt is left, that hands the race to _retry_lock_race.
# ---------------------------------------------------------------------------------------

LOCK_RACE_ATTEMPTS = 3
# A queued sale whose attempts all lose goes to the back of the queue this many times before it
# is failed: a pile-up of submits can hold its locks for minutes, far past the attempts above.
LOCK_RACE_REQUEUES = 5
LOCK_RACE_ERRORS = (frappe.QueryDeadlockError, frappe.QueryTimeoutError)


def _retry_lock_race(fn, *args):
	"""Run fn, and run it again from a clean transaction when it loses a lock race."""
	# Retry only from a clean start: inside another retry, or after the caller has written,
	# the rollback would take work that is not fn's with it.
	if frappe.flags.klik_lock_race_retry is not None or frappe.db.transaction_writes:
		return fn(*args)
	try:
		for attempt in range(1, LOCK_RACE_ATTEMPTS + 1):
			retry_left = attempt < LOCK_RACE_ATTEMPTS
			frappe.flags.klik_lock_race_retry = retry_left
			try:
				# A copy each time: an attempt may have changed the payload it was given.
				return fn(*copy.deepcopy(args))
			except LOCK_RACE_ERRORS:
				if not retry_left:
					raise
				frappe.db.rollback()
				# Jittered, so two tills that collided do not collide again.
				time.sleep(random.uniform(0.5, 2))
	finally:
		frappe.flags.klik_lock_race_retry = None


def _rollback_to_savepoint(save_point):
	"""Undo back to save_point. False when it was already gone, with everything since the
	transaction began: a deadlock rolls back the whole transaction, savepoints included."""
	try:
		frappe.db.rollback(save_point=save_point)
		return True
	except Exception as e:
		if not (e.args and e.args[0] == 1305):  # ER_SP_DOES_NOT_EXIST
			raise
		frappe.db.rollback()
		return False


def _raise_lock_race(error):
	"""Hand a lock race to _retry_lock_race while it has an attempt left; else do nothing."""
	if frappe.flags.klik_lock_race_retry and isinstance(error, LOCK_RACE_ERRORS):
		raise error


def _abort_checkout(state, error):
	"""Fail a checkout without burning a receipt number. The step order is load-bearing.

	Read the "naming-counter burn guard" comment above before changing anything here.
	"""
	# 0. A lock race with an attempt left is retried whole, not failed.
	_raise_lock_race(error)

	# 1. UNDO - first, always. Everything written before this point is discarded by it.
	if state.savepoint:
		# The invoice exists. Undo the half-finished submit but keep the draft and the
		# number it legitimately consumed, so Invoice History can retry it.
		if not _rollback_to_savepoint(state.savepoint):
			# A lock race took the invoice with the whole transaction, and its number went
			# back with it: there is no draft to retry, so do not name one.
			state.invoice_name = None
			state.savepoint = None
	else:
		# No invoice was ever written, so the naming counter increment is this checkout's
		# only trace. A full rollback returns the receipt number to the sequence.
		frappe.db.rollback()

	# 2. RECORD - after the undo, or the rollback eats the record.
	#    Bookkeeping must never replace the real error with one of its own.
	try:
		_record_failed_checkout_request(state, error)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Checkout Request Bookkeeping Error")

	# 3. LOG - after the undo, for the same reason.
	frappe.log_error(frappe.get_traceback(), "Submit Invoice Error")

	# 4. RESPOND
	response = {"success": False, "message": error_reason(error)}
	if state.checkout_request_id:
		response["checkout_request_id"] = state.checkout_request_id
	if state.invoice_name:
		response["invoice_name"] = state.invoice_name
		response["invoice_id"] = state.invoice_name
	return response


def _checkout_invoice_state(doc):
	"""Where a recorded invoice actually got to, from the invoice itself."""
	queue_status = str(getattr(doc, "queue_status", "") or "").strip().lower()
	if queue_status == QUEUE_STATUSES["failed"].lower():
		return "failed"
	if doc.docstatus == 1 or queue_status == QUEUE_STATUSES["submitted"].lower():
		return "submitted"
	if queue_status in (QUEUE_STATUSES["queued"].lower(), QUEUE_STATUSES["processing"].lower()):
		return queue_status
	return "accepted"


def _existing_checkout_response(doc, checkout_request_id=None):
	state = _checkout_invoice_state(doc)
	response = {
		"success": state != "failed",
		"idempotent_replay": True,
		"checkout_status": state,
		"checkout_request_id": checkout_request_id,
		"queue_status": getattr(doc, "queue_status", None),
		"invoice_name": doc.name,
		"invoice_id": doc.name,
		"invoice": _get_invoice_response_summary(doc),
		"payment_entry": None,
	}
	if state == "failed":
		response["message"] = getattr(doc, "queue_error", None) or _(
			"This checkout already created invoice {0}, but its submission failed. Retry it from Invoice History."
		).format(doc.name)
	return response


def _checkout_request_response(request):
	"""The answer to 'what happened to this checkout' — for a replay or a status poll."""
	if request.sales_invoice and frappe.db.exists("Sales Invoice", request.sales_invoice):
		response = _existing_checkout_response(
			frappe.get_doc("Sales Invoice", request.sales_invoice),
			checkout_request_id=request.name,
		)
		if request.status == "Failed":
			response["success"] = False
			response["checkout_status"] = "failed"
			response["message"] = request.error_message or _(
				"This checkout created invoice {0}, but submission failed. Retry it from Invoice History."
			).format(request.sales_invoice)
		return response

	if request.status == "Failed":
		return {
			"success": False,
			"idempotent_replay": True,
			"checkout_status": "failed",
			"checkout_request_id": request.name,
			"message": request.error_message or _("The previous checkout attempt failed."),
		}

	# Claimed but no document yet: the first call is still in flight, or died before it
	# saved anything. Either way the client must wait rather than send the cart again.
	return {
		"success": False,
		"idempotent_replay": True,
		"checkout_status": "processing",
		"checkout_request_id": request.name,
		"message": _("This checkout is still being processed. Please wait while its status is recovered."),
	}


def _set_checkbox_field_value(doc, fieldname, value):
	"""Safely set checkbox fields only when present on the document."""
	if not doc or not hasattr(doc, fieldname):
		return
	setattr(doc, fieldname, cint(bool(value)))


def _enforce_submit_permission(doc, user=None):
	"""Explicitly check submit permission for `doc`.

	Several call sites use `save(ignore_permissions=True)` earlier in the same
	flow, which sets `doc.flags.ignore_permissions = True` on the instance and
	causes the framework's own permission check inside `doc.submit()` to be
	skipped silently. `frappe.has_permission` goes straight to the permission
	engine and ignores that flag, so it still enforces whatever the Role
	Permission Manager says for the acting user.
	"""
	if not frappe.has_permission(doc.doctype, "submit", doc=doc, user=user):
		frappe.throw(
			_("You do not have permission to submit {0} {1}").format(_(doc.doctype), doc.name),
			frappe.PermissionError,
		)


def _apply_klik_invoice_flags(doc, is_held=None, is_submitted=None):
	"""Track Klik-origin invoices and hold/submission state using custom checkbox fields."""
	if not doc:
		return

	_set_checkbox_field_value(doc, "custom_is_created_from_klik", 1)

	if is_held is not None:
		_set_checkbox_field_value(doc, "custom_is_held", is_held)

	if is_submitted is not None:
		_set_checkbox_field_value(doc, "custom_is_submitted", is_submitted)


def _powerpack_floor_installed():
	return "cecypo_powerpack" in frappe.get_installed_apps() and frappe.get_meta("POS Profile").has_field(
		"powerpack_enforce_list_floor"
	)


def _price_floor_report(doc):
	"""cecypo_powerpack's minimum-price lines for this till, or nothing when it is not installed."""
	if not _powerpack_floor_installed():
		return {"enforced": False, "rows": []}
	from cecypo_powerpack.pos_list_floor import till_floor_report

	return till_floor_report(doc)


def _apply_price_authorisation(doc, data):
	"""A supervisor's authorisation from the till, checked again by PowerPack when the invoice saves."""
	token = data.get("price_authorisation") if isinstance(data, dict) else None
	if token and _powerpack_floor_installed():
		from cecypo_powerpack.pos_supervisor_pin import apply_authorisation

		apply_authorisation(doc, token)


def _refuse_unapproved_price_breach(doc):
	"""Stop a checkout the minimum-selling-price rule would only fail later.

	PowerPack lets a breaching draft save so it can be routed for approval. A POS sale is
	never meant to wait as a draft, and in background mode the submit would fail after the
	cashier was told the sale went through. Called before the invoice counts as persisted,
	so _abort_checkout rolls the whole checkout back.
	"""
	if not doc.meta.has_field("powerpack_price_breach"):
		return
	if cint(doc.get("powerpack_price_breach")) and not doc.get("powerpack_price_approved_rows"):
		frappe.throw(_("Prices below the minimum need approval. Hold the order to request it."))


@contextmanager
def _keeping_naming_series():
	"""Delete without winding naming series back.

	frappe rewinds a series whenever the document holding its newest number is deleted, so
	clearing held orders newest-first sent the numbering back to SO-00001 and the same names
	were reused (and deleted again) several times a day - and an invoice's link to the order
	it came from could point at a later order of the same name. Any counter that went back
	is put where it was; one that moved on meanwhile is left alone.
	"""
	before = dict(frappe.db.sql("SELECT `name`, `current` FROM `tabSeries`"))
	try:
		yield
	finally:
		for name, current in frappe.db.sql("SELECT `name`, `current` FROM `tabSeries`"):
			if name in before and cint(current) < cint(before[name]):
				frappe.db.sql(
					"UPDATE `tabSeries` SET `current` = %s WHERE `name` = %s AND `current` < %s",
					(before[name], name, before[name]),
				)


# Filled from the customer by ERPNext's set_missing_values - but only when empty, so a document
# whose customer changes keeps the old customer's. Its validation then refuses the address:
# "Billing Address does not belong to the <new customer>".
_PARTY_DETAIL_FIELDS = (
	"customer_name",
	"customer_address",
	"address_display",
	"shipping_address_name",
	"shipping_address",
	"contact_person",
	"contact_display",
	"contact_mobile",
	"contact_email",
	"contact_phone",
	"territory",
	"customer_group",
	"tax_category",
	# The receivable: left as is, a switch posts it to the old customer's account.
	"debit_to",
	"party_account_currency",
)


def _set_customer(doc, customer):
	"""Point an existing held order or draft at `customer`, forgetting what was filled from
	the previous one so set_missing_values fills it again for the new one."""
	if doc.get("customer") and doc.customer != customer:
		for fieldname in _PARTY_DETAIL_FIELDS:
			if doc.meta.has_field(fieldname):
				doc.set(fieldname, None)
	doc.customer = customer


def _apply_walkin_party_fields(doc, walkin_name=None, walkin_phone=None, walkin_tax_id=None):
	"""Set walk-in buyer name/phone/PIN on a selling doc when the optional custom
	fields exist. tax_id itself is still set by the standard field path; the PIN also goes in
	custom_walkin_tax_id (walk-in customers only), which overrides.walkin_tax_id copies back
	into tax_id on every save, so it survives draft saves and Quotation -> Order -> Invoice."""
	if walkin_name and doc.meta.has_field("custom_walkin_customer_name"):
		doc.custom_walkin_customer_name = walkin_name
	if walkin_phone and doc.meta.has_field("custom_walkin_phone"):
		doc.custom_walkin_phone = walkin_phone
	if (
		walkin_tax_id
		and doc.meta.has_field("custom_walkin_tax_id")
		and frappe.db.get_value("Customer", getattr(doc, "customer", None), "custom_is_walkin")
	):
		doc.custom_walkin_tax_id = walkin_tax_id


def _carry_tax_id_from_original(return_doc, original_invoice):
	"""Put the sale's tax_id back on the credit note after its draft save.

	Sales Invoice.tax_id is fetched from Customer.tax_id on every draft save, so a tax ID
	captured at the till for a walk-in (whose Customer record has none) is wiped by
	return_doc.save(). Submit does not re-fetch, so a direct write before it sticks - the
	same trick create_and_submit_invoice and hold_invoice use for the sale itself."""
	tax_id = original_invoice.get("tax_id")
	if tax_id and return_doc.get("tax_id") != tax_id:
		return_doc.db_set("tax_id", tax_id)


def _negate_actual_charges(return_doc):
	"""Flip fixed ("Actual") tax rows on a credit note, the way ERPNext's make_return_doc does.

	Percentage rows follow the negative item amounts through calculate_taxes_and_totals; a
	fixed row (a Shipping Rule's courier fee) is copied as-is by the mapper and would stay
	positive, shrinking the credit note and making ERPNext reject the refund row. Nothing
	re-applies a shipping rule on a POS return, so the flip sticks."""
	for tax in return_doc.get("taxes") or []:
		if tax.charge_type == "Actual":
			tax.tax_amount = -1 * flt(tax.tax_amount)


def _negate_order_discount(return_doc, original_invoice):
	"""Reverse the order discount on a credit note, the way ERPNext's make_return_doc does.

	The mapper copies discount_amount positive, which calculate_taxes_and_totals subtracts
	from the negative items: a sale of 560 discounted to 11.20 came back as a 1,108.80 credit.
	A partial return takes the discount's share of the value it brings back. Call it once
	the return's item quantities are final."""
	original_total = flt(original_invoice.total)
	if not flt(return_doc.discount_amount) or not original_total:
		return
	returned = sum(abs(flt(item.qty)) * flt(item.rate) for item in return_doc.items)
	return_doc.discount_amount = -flt(
		flt(original_invoice.discount_amount) * returned / original_total,
		return_doc.precision("discount_amount"),
	)


def _returns_every_line(original_invoice, return_doc):
	"""True when this return alone brings back every quantity on the original invoice."""
	sold = {}
	for row in original_invoice.items:
		sold[row.item_code] = sold.get(row.item_code, 0.0) + abs(flt(row.qty))
	returned = {}
	for row in return_doc.items:
		returned[row.item_code] = returned.get(row.item_code, 0.0) + abs(flt(row.qty))
	return all(flt(returned.get(code, 0.0)) >= qty for code, qty in sold.items())


def _finishes_the_sale(original_invoice, return_doc):
	"""True when this return, with the sale's earlier ones, brings back every quantity sold."""
	prior = frappe.get_all(
		"Sales Invoice", filters={"is_return": 1, "docstatus": 1, "return_against": original_invoice.name}, pluck="name"
	)
	rows = (
		frappe.get_all("Sales Invoice Item", filters={"parent": ["in", prior]}, fields=["sales_invoice_item", "item_code", "qty"])
		if prior
		else []
	)
	# By sale line; a note from before lines were linked counts by item code.
	back = {}
	for row in [*rows, *return_doc.items]:
		key = row.sales_invoice_item or row.item_code
		back[key] = back.get(key, 0.0) + abs(flt(row.qty))
	# The delivery fee line is a charge, not goods: it never comes back on its own.
	delivery = (frappe.db.get_value("POS Profile", original_invoice.pos_profile, "custom_delivery_charge_item") or "").strip() if original_invoice.pos_profile and frappe.get_meta("POS Profile").has_field("custom_delivery_charge_item") else ""
	sold = [row for row in original_invoice.items if not delivery or row.item_code != delivery]
	return all(flt(back.get(row.name)) + flt(back.get(row.item_code)) >= abs(flt(row.qty)) for row in sold)


def _drop_actual_charges(return_doc):
	"""Leave fixed charges off a partial credit note.

	The delivery happened whether or not one item comes back, so a partial return keeps
	the courier fee: the note carries the returned items and their percentage taxes only.
	A return of every quantity reverses the fee through _negate_actual_charges."""
	return_doc.set("taxes", [t for t in return_doc.get("taxes") or [] if t.charge_type != "Actual"])


def _fixed_charges_for(invoice_names):
	"""Fixed ("Actual") charge rows per invoice: what a Shipping Rule put on the sale."""
	if not invoice_names:
		return {}
	rows = frappe.get_all(
		"Sales Taxes and Charges",
		filters={"parent": ["in", list(invoice_names)], "parenttype": "Sales Invoice", "charge_type": "Actual"},
		fields=["parent", "description", "account_head", "tax_amount"],
		order_by="parent, idx",
	)
	out = {}
	for row in rows:
		if not flt(row.tax_amount):
			continue
		out.setdefault(row.parent, []).append(
			{"description": row.description, "account_head": row.account_head, "amount": flt(row.tax_amount)}
		)
	return out


def _reversed_fixed_charges(invoice_names):
	"""{invoice: {account_head: credit note}} for fixed charges an earlier submitted return
	already reversed. Like returned_qty for items: a fee goes back once."""
	if not invoice_names:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT si.return_against AS original, si.name AS credit_note, t.account_head
		FROM `tabSales Invoice` si
		JOIN `tabSales Taxes and Charges` t ON t.parent = si.name
		WHERE si.is_return = 1 AND si.docstatus = 1 AND si.return_against IN %(names)s
		  AND t.charge_type = 'Actual' AND t.tax_amount < 0
		ORDER BY si.name
		""",
		{"names": tuple(invoice_names)},
		as_dict=True,
	)
	out = {}
	for row in rows:
		out.setdefault(row.original, {}).setdefault(row.account_head, row.credit_note)
	return out


def _refuse_second_fee_reversal(invoice_name):
	already = _reversed_fixed_charges([invoice_name]).get(invoice_name) or {}
	if already:
		frappe.throw(
			_("The fixed charges on {0} were already reversed by {1}.").format(
				frappe.bold(invoice_name), ", ".join(sorted(set(already.values())))
			)
		)


def _parse_extra_fields(data):
	"""Extract the generic POS extra-fields map from a request payload."""
	if not isinstance(data, dict):
		return {}
	raw = data.get("extra_fields")
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except Exception:
			raw = {}
	return raw if isinstance(raw, dict) else {}


def _apply_extra_fields(doc, extra_fields, allowed=None):
	"""Set each configured POS extra field on the document, guarded.

	Writes only fields in the server-resolved allow-list (the POS Profile's
	configured + eligible fields), that exist on the doctype, and have a
	non-empty value. The allow-list stops a client from setting arbitrary
	fields (e.g. invoice discounts) via the extra_fields map. Tests pass
	``allowed`` explicitly; production resolves it from the active POS Profile.
	"""
	if allowed is None:
		from klik_pos.api.pos_profile import get_configured_extra_fieldnames
		allowed = get_configured_extra_fieldnames()
	for fieldname, value in (extra_fields or {}).items():
		if fieldname not in allowed:
			continue
		if value in (None, "") or not doc.meta.has_field(fieldname):
			continue
		doc.set(fieldname, value)


def validate_required_salesperson(doc):
	"""Enforce salesperson presence for POS flows when the POS profile requires it.

	Not on a return: the credit note carries the sale's salesperson, and the return
	endpoints take none - a till requiring a PIN would otherwise refuse to refund a sale
	rung where none was needed."""
	if not doc or not getattr(doc, "is_pos", 0) or cint(getattr(doc, "is_return", 0)):
		return

	pos_profile_name = getattr(doc, "pos_profile", None)
	if not pos_profile_name:
		return

	require_sales_person = frappe.db.get_value(
		"POS Profile",
		pos_profile_name,
		"custom_sales_person_pin_required",
	)
	if not cint(require_sales_person):
		return

	sales_team = getattr(doc, "sales_team", None) or []
	has_salesperson = any(
		(row.get("sales_person") if isinstance(row, dict) else getattr(row, "sales_person", None))
		for row in sales_team
	)
	if has_salesperson:
		return

	frappe.throw(
		_("Sales person is mandatory to complete this sale. Please enter a valid salesperson PIN before continuing.")
	)

def _is_return_allowed_for_current_profile():
	"""Return True unless POS Profile explicitly disables returns."""
	try:
		pos_profile = get_current_pos_profile()
		allow_return = getattr(pos_profile, "custom_allow_return", None)
		if allow_return in (0, "0", False):
			return False
		return True
	except Exception:
		# Keep backward compatibility: do not block returns when profile resolution fails.
		return True


def _ensure_return_allowed():
	if not _is_return_allowed_for_current_profile():
		frappe.throw(_("Returns are disabled for the current POS Profile."))


def _compute_net_cash_held(grand_total, outstanding_amount, credit_already_applied, cash_already_refunded):
	"""Cash the customer actually handed over and we are still holding.

	This is the ceiling for a refund. It is NOT ``paid_amount``: that field is 0 for invoices
	settled by a Payment Entry, and it takes no account of earlier returns.

	``grand_total - outstanding`` covers both POS payment rows and Payment Entries, but it
	double-counts outstanding that earlier credit notes knocked off (``credit_already_applied``)
	and ignores cash handed back by earlier returns (``cash_already_refunded``).
	"""
	held = (
		flt(grand_total) - flt(outstanding_amount) - flt(credit_already_applied) - flt(cash_already_refunded)
	)
	return max(0.0, held)


def _get_prior_return_adjustments(invoice_name):
	"""(credit_already_applied, cash_already_refunded) from submitted returns against an invoice."""
	prior_returns = frappe.get_all(
		"Sales Invoice",
		filters={"return_against": invoice_name, "is_return": 1, "docstatus": 1},
		fields=["grand_total", "paid_amount", "update_outstanding_for_self"],
	)

	credit_already_applied = 0.0
	cash_already_refunded = 0.0
	for row in prior_returns:
		# Only returns that pushed their credit onto the original reduced its outstanding.
		if not cint(row.update_outstanding_for_self):
			credit_already_applied += abs(flt(row.grand_total))
		cash_already_refunded += abs(flt(row.paid_amount))

	return credit_already_applied, cash_already_refunded


def _is_cash_mode(mode_of_payment):
	"""A Cash-type Mode of Payment: money in the drawer, the only kind the till can hand back.
	Card, M-Pesa and bank money go back through accounts, as a Payment Entry reversal."""
	return bool(mode_of_payment) and frappe.get_cached_value("Mode of Payment", mode_of_payment, "type") == "Cash"


def _cash_row(invoice):
	"""The invoice's first Cash-type payment row that took money, or None."""
	return next(
		(p for p in invoice.get("payments") or [] if flt(p.amount) > 0 and _is_cash_mode(p.mode_of_payment)),
		None,
	)


def _cash_kept(invoice):
	"""Cash the drawer kept from a sale: its Cash-type rows, less the change handed back."""
	tendered = sum(flt(p.amount) for p in invoice.get("payments") or [] if _is_cash_mode(p.mode_of_payment))
	if tendered <= 0:
		return 0.0
	return max(0.0, tendered - flt(invoice.get("change_amount")))


def _cash_refunded_by_prior_returns(invoice_name):
	"""Cash already handed back by earlier returns against ``invoice_name``."""
	return flt(
		frappe.db.sql(
			"""SELECT SUM(ABS(sip.amount)) FROM `tabSales Invoice Payment` sip
			INNER JOIN `tabSales Invoice` si ON si.name = sip.parent
			INNER JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
			WHERE si.return_against = %s AND si.is_return = 1 AND si.docstatus = 1 AND mop.type = 'Cash'""",
			invoice_name,
		)[0][0]
	)


def _get_refundable_cash(original_invoice, return_doc=None):
	"""Maximum cash that may be paid back for a return against ``original_invoice``.

	A credit sale returns 0 here, so its return carries no payment rows at all and the credit
	note keeps a real outstanding balance that can be allocated against the invoice.

	Non-POS returns also return 0: ERPNext's ``set_paid_amount`` unconditionally clears
	``payments`` on a non-POS return, so a payment row there would be silently dropped and we
	would report a refund that never happened. Refunding a non-POS credit note is a separate
	Payment Entry against the credit note.

	Money that settled the invoice as an advance is not cash either. An M-Pesa sale is
	settled entirely that way - the drawer took nothing - so ``total_advance`` comes off the
	ceiling; what goes back to that customer is the credit note, or a refund against the
	Payment Entry that brought the money in.
	"""
	if return_doc is not None and not cint(return_doc.is_pos):
		return 0.0

	credit_already_applied, cash_already_refunded = _get_prior_return_adjustments(original_invoice.name)
	held = _compute_net_cash_held(
		original_invoice.grand_total,
		original_invoice.outstanding_amount,
		credit_already_applied,
		cash_already_refunded,
	)
	paid_back_ceiling = held - flt(getattr(original_invoice, "total_advance", 0))
	# And only cash goes back at the till: card, M-Pesa and bank money is refunded by accounts
	# (a Payment Entry reversal), so it stays on the credit note as the customer's credit.
	cash_ceiling = _cash_kept(original_invoice) - _cash_refunded_by_prior_returns(original_invoice.name)
	return max(0.0, min(paid_back_ceiling, cash_ceiling))


def _allocate_return_against_original(return_doc, original_invoice, refunded_cash):
	"""Point the credit note's leftover balance at the original invoice where it fits.

	ERPNext will not do this for us: ``validate_against_voucher_outstanding`` early-returns for
	POS documents, so it neither warns nor caps ``update_outstanding_for_self`` on POS returns.
	"""
	precision = return_doc.precision("grand_total") or 2
	return_total = abs(flt(return_doc.rounded_total or return_doc.grand_total, precision))
	remainder = flt(return_total - flt(refunded_cash), precision)

	if remainder <= 0:
		# Fully refunded in cash - nothing left to allocate.
		return

	original_outstanding = flt(original_invoice.outstanding_amount, precision)
	if remainder <= original_outstanding:
		# Knock the credit straight off the original invoice.
		return_doc.update_outstanding_for_self = 0
	else:
		# Would drive the original negative; leave the credit on the note itself so it shows up
		# in Payment Reconciliation instead.
		return_doc.update_outstanding_for_self = 1

def _coerce_queue_status(status):
	if not status:
		return QUEUE_STATUSES["queued"]
	if isinstance(status, str):
		normalized = status.strip().lower()
		return QUEUE_STATUSES.get(normalized, status)
	return QUEUE_STATUSES["queued"]


def _truncate_queue_error(error_message, max_length=900):
	"""Plain-text, length-capped version of a queue failure reason.

	frappe.throw messages routinely carry markup ("Insufficient stock for item
	<strong>X</strong>..."), and this text is rendered as text - in a toast, a banner and the
	invoice view - so the tags would otherwise show up literally in front of the cashier.
	"""
	message = strip_html_tags(str(error_message or "")).strip()
	if len(message) <= max_length:
		return message
	return f"{message[:max_length].rstrip()}..."


def get_reserved_stock_map(item_codes=None, warehouse=None, exclude_invoice=None):
	"""Return reserved stock from Stock Reservation Entries keyed by (item_code, warehouse)."""
	conditions = [
		"sre.docstatus = 1",
		"sre.delivered_qty < sre.reserved_qty",
		"IFNULL(sre.item_code, '') != ''",
		"IFNULL(sre.warehouse, '') != ''",
	]
	params = []

	if item_codes:
		item_placeholders = ", ".join(["%s"] * len(item_codes))
		conditions.append(f"sre.item_code IN ({item_placeholders})")
		params.extend(list(item_codes))

	if warehouse:
		conditions.append("sre.warehouse = %s")
		params.append(warehouse)

	if exclude_invoice:
		conditions.append("NOT (sre.voucher_type = 'Sales Invoice' AND sre.voucher_no = %s)")
		params.append(exclude_invoice)

	query = f"""
		SELECT
			sre.item_code,
			sre.warehouse,
			SUM(COALESCE(sre.reserved_qty, 0) - COALESCE(sre.delivered_qty, 0) - COALESCE(sre.transferred_qty, 0) - COALESCE(sre.consumed_qty, 0)) AS reserved_qty
		FROM `tabStock Reservation Entry` sre
		WHERE {' AND '.join(conditions)}
		GROUP BY sre.item_code, sre.warehouse
	"""

	rows = frappe.db.sql(query, tuple(params), as_dict=True)
	reserved_map = {}
	for row in rows:
		reserved_map[(row.item_code, row.warehouse)] = flt(row.reserved_qty or 0)

	return reserved_map


def _get_sales_invoice_reservation_map(invoice_name, item_codes=None, warehouse=None):
	"""Return reservation qty map for one Sales Invoice from Stock Reservation Entry."""
	if not invoice_name:
		return {}

	conditions = [
		"docstatus = 1",
		"voucher_type = 'Sales Invoice'",
		"voucher_no = %s",
		"delivered_qty < reserved_qty",
	]
	params = [invoice_name]

	if item_codes:
		placeholders = ", ".join(["%s"] * len(item_codes))
		conditions.append(f"item_code IN ({placeholders})")
		params.extend(list(item_codes))

	if warehouse:
		conditions.append("warehouse = %s")
		params.append(warehouse)

	query = f"""
		SELECT
			item_code,
			warehouse,
			SUM(COALESCE(reserved_qty, 0) - COALESCE(delivered_qty, 0) - COALESCE(transferred_qty, 0) - COALESCE(consumed_qty, 0)) AS reserved_qty
		FROM `tabStock Reservation Entry`
		WHERE {' AND '.join(conditions)}
		GROUP BY item_code, warehouse
	"""

	rows = frappe.db.sql(query, tuple(params), as_dict=True)
	return {(row.item_code, row.warehouse): flt(row.reserved_qty or 0) for row in rows}


def _cancel_sales_invoice_reservations(invoice_name):
	"""Cancel active Stock Reservation Entries for a Sales Invoice.

	Selects exactly what erpnext's cancel_stock_reservation_entries would, but cancels
	each entry with flags.ignore_permissions so that releasing a reservation costs no
	more rights than creating one did — _reserve_stock_for_queued_invoice already
	submits these entries with ignore_permissions.

	Without the flag, erpnext's helper does a bare frappe.get_doc(...).cancel(), whose
	write check fails for any role without Stock Reservation Entry rights (every Express
	role, for one). That raises inside before_submit, aborts the whole submit, and
	strands the invoice in the queue with its stock still held — the reservation can be
	taken but never given back.
	"""
	if not invoice_name:
		return

	sre = frappe.qb.DocType("Stock Reservation Entry")
	rows = (
		frappe.qb.from_(sre)
		.select(sre.name)
		.where(
			(sre.docstatus == 1)
			& (sre.voucher_type == "Sales Invoice")
			& (sre.voucher_no == invoice_name)
		)
		.orderby(sre.creation)
	).run(as_dict=True)

	for row in rows:
		entry = frappe.get_doc("Stock Reservation Entry", row.name)
		entry.flags.ignore_permissions = True
		entry.cancel()


def _stock_reservation_enabled():
	"""Whether this site permits stock reservation at all.

	ERPNext gates every reservation it creates on this switch (see
	validate_stock_reservation_settings), and additionally refuses any voucher type
	other than Sales Order. klik_pos builds its Sales Invoice entries by hand, so it
	honours neither by default — meaning a site with reservation switched off still
	accumulated entries. Respecting the switch makes it the real control it appears to
	be, and lets a deployment that should not reserve (ERPNext Express, whose roles hold
	no Stock Reservation Entry rights) simply turn it off.
	"""
	return bool(frappe.get_cached_value("Stock Settings", None, "enable_stock_reservation"))


def negative_stock_allowed(item_flag=0):
	"""ERPNext's rule (stock_ledger.is_negative_stock_allowed): the Stock Settings switch
	allows negative stock for every item; otherwise only items with their own flag."""
	return bool(cint(item_flag) or cint(frappe.get_cached_value("Stock Settings", None, "allow_negative_stock")))


def _should_reserve_stock(doc):
	return bool(getattr(doc, "reserve_stock", 0))


def _reserve_stock_for_queued_invoice(doc):
	"""Create Stock Reservation Entry rows for queued Sales Invoice draft rows."""
	if not doc or doc.doctype != "Sales Invoice" or doc.docstatus != 0:
		return
	if not _should_reserve_stock(doc):
		return
	if not _stock_reservation_enabled():
		return
	if getattr(doc, "is_return", 0):
		return

	_cancel_sales_invoice_reservations(doc.name)

	item_codes = list({row.item_code for row in doc.items if row.item_code})
	item_meta_map = {}
	if item_codes:
		item_meta_map = {
			row.name: row
			for row in frappe.get_all(
				"Item",
				filters={"name": ["in", item_codes]},
				fields=["name", "is_stock_item", "has_serial_no", "has_batch_no", "stock_uom", "allow_negative_stock"],
			)
		}

	for row in doc.items:
		if not row.item_code or not row.warehouse:
			continue

		item_meta = item_meta_map.get(row.item_code)
		if not item_meta or not int(item_meta.is_stock_item or 0):
			continue
		# Items allowed to go negative are exempt from reservation — nothing to reserve.
		if negative_stock_allowed(item_meta.allow_negative_stock):
			continue

		required_qty = flt(abs(getattr(row, "stock_qty", 0) or 0))
		if required_qty <= 0:
			required_qty = flt(abs(getattr(row, "qty", 0) or 0))
		if required_qty <= 0:
			continue

		actual_qty = flt(
			frappe.db.get_value(
				"Bin",
				{"item_code": row.item_code, "warehouse": row.warehouse},
				"actual_qty",
			)
			or 0
		)
		reserved_map = get_reserved_stock_map(
			item_codes=[row.item_code],
			warehouse=row.warehouse,
			exclude_invoice=doc.name,
		)
		available_to_reserve = flt(actual_qty - reserved_map.get((row.item_code, row.warehouse), 0))

		if required_qty > available_to_reserve + 1e-9:
			frappe.throw(
				_(
					"Insufficient stock to reserve for item {0} in warehouse {1}. Required: {2}, Available to reserve: {3}."
				).format(
					frappe.bold(row.item_code),
					frappe.bold(row.warehouse),
					flt(required_qty),
					flt(available_to_reserve),
				)
			)

		sre = frappe.new_doc("Stock Reservation Entry")
		sre.item_code = row.item_code
		sre.warehouse = row.warehouse
		sre.has_serial_no = int(item_meta.has_serial_no or 0)
		sre.has_batch_no = int(item_meta.has_batch_no or 0)
		sre.voucher_type = "Sales Invoice"
		sre.voucher_no = doc.name
		sre.voucher_detail_no = row.name
		sre.available_qty = available_to_reserve
		sre.voucher_qty = required_qty
		sre.reserved_qty = required_qty
		sre.company = doc.company
		sre.stock_uom = row.stock_uom or item_meta.stock_uom
		sre.project = doc.project
		sre.save(ignore_permissions=True)
		sre.submit()


def get_reserved_qty_for_item_warehouse(item_code, warehouse, exclude_invoice=None):
	reserved_map = get_reserved_stock_map(
		item_codes=[item_code],
		warehouse=warehouse,
		exclude_invoice=exclude_invoice,
	)
	return flt(reserved_map.get((item_code, warehouse), 0))


def get_available_stock_map(keys, exclude_invoice=None):
	"""{(item_code, warehouse): actual_qty, reserved_qty, available_qty} for each key - available
	being net of Stock Reservation Entries, the figure both the oversell refusal and Loss of Sale
	measure against. A key with no Bin has none."""
	keys = set(keys)
	if not keys:
		return {}
	bins = frappe.get_all(
		"Bin",
		filters={
			"item_code": ["in", list({key[0] for key in keys})],
			"warehouse": ["in", list({key[1] for key in keys})],
		},
		fields=["item_code", "warehouse", "actual_qty"],
	)
	actual_map = {(row.item_code, row.warehouse): flt(row.actual_qty or 0) for row in bins}
	reserved_map = get_reserved_stock_map(
		item_codes=list({key[0] for key in keys}),
		exclude_invoice=exclude_invoice,
	)
	stock = {}
	for key in keys:
		actual_qty = flt(actual_map.get(key, 0))
		reserved_qty = flt(reserved_map.get(key, 0))
		stock[key] = frappe._dict(
			actual_qty=actual_qty, reserved_qty=reserved_qty, available_qty=flt(actual_qty - reserved_qty)
		)
	return stock


def _validate_reserved_stock_for_items(doc, exclude_invoice=None):
	"""Validate available stock, net of anything held by Stock Reservation Entries.

	Deliberately NOT gated on _should_reserve_stock. Whether the site reserves stock and
	whether it cares about overselling are different questions: get_reserved_stock_map simply
	returns nothing when there are no reservations, so the check degrades to a plain
	availability test rather than switching itself off.

	It used to bail on that gate, which had two consequences. On the checkout preview the
	call was dead outright - build_sales_invoice_doc never sets reserve_stock, so it returned
	immediately and stock was never validated before payment. And once reservation became
	conditional on the Stock Settings switch, the queue path stopped validating too on any
	site with reservation turned off, so an oversell that used to be blocked at checkout
	would go through and fail at submit instead.
	"""
	if not getattr(doc, "items", None):
		return

	item_codes_in_doc = list({row.item_code for row in doc.items if row.item_code})
	item_meta_map = {}
	if item_codes_in_doc:
		item_meta_map = {
			row.name: row
			for row in frappe.get_all(
				"Item",
				filters={"name": ["in", item_codes_in_doc]},
				fields=["name", "is_stock_item", "allow_negative_stock"],
			)
		}

	required_qty_map = {}

	for row in doc.items:
		if not row.item_code or not row.warehouse:
			continue
		item_meta = item_meta_map.get(row.item_code)
		# Skip non-stock items and items allowed to go negative (no reservation enforced).
		if item_meta and not int(item_meta.is_stock_item or 0):
			continue
		if item_meta and negative_stock_allowed(item_meta.allow_negative_stock):
			continue

		required_qty = flt(abs(getattr(row, "stock_qty", 0) or 0))
		if required_qty <= 0:
			required_qty = flt(abs(getattr(row, "qty", 0) or 0))
		if required_qty <= 0:
			continue

		key = (row.item_code, row.warehouse)
		required_qty_map[key] = flt(required_qty_map.get(key, 0) + required_qty)

	if not required_qty_map:
		return

	stock = get_available_stock_map(required_qty_map.keys(), exclude_invoice=exclude_invoice)

	insufficient = []
	for key, required_qty in required_qty_map.items():
		actual_qty = stock[key].actual_qty
		reserved_qty = stock[key].reserved_qty
		available_qty = stock[key].available_qty

		if required_qty > available_qty + 1e-9:
			insufficient.append(
				{
					"item_code": key[0],
					"warehouse": key[1],
					"required_qty": required_qty,
					"available_qty": available_qty,
					"actual_qty": actual_qty,
					"reserved_qty": reserved_qty,
				}
			)

	if insufficient:
		first = insufficient[0]
		frappe.throw(
			_(
				"Insufficient stock for item {0} in warehouse {1}. Required: {2}, Available (after stock reservations): {3}, Reserved: {4}."
			).format(
				frappe.bold(first["item_code"]),
				frappe.bold(first["warehouse"]),
				flt(first["required_qty"]),
				flt(first["available_qty"]),
				flt(first["reserved_qty"]),
			)
		)


def _update_queue_fields(doc, status, error_message=None, attempts=None):
	doc.queue_status = _coerce_queue_status(status)
	# Keep held flag unchanged for audit history; only update submitted state here.
	_apply_klik_invoice_flags(doc, is_submitted=(doc.queue_status == QUEUE_STATUSES["submitted"]))
	if hasattr(doc, "queue_error"):
		doc.queue_error = _truncate_queue_error(error_message) if error_message else ""
	if hasattr(doc, "queue_attempts") and attempts is not None:
		doc.queue_attempts = attempts
	if hasattr(doc, "queue_last_attempt_at") and status == QUEUE_STATUSES["processing"]:
		doc.queue_last_attempt_at = frappe.utils.now_datetime()


def _get_queue_failure_user_ids(requested_by=None):
	"""Enabled users who should hear about a failed queued invoice: the cashier and managers.

	Returns User names, not email addresses. frappe.publish_realtime targets a user by name and
	the two are not guaranteed to match, so the realtime push needs this list rather than the
	address list _get_queue_failure_recipients derives from it.
	"""
	user_ids = []

	if requested_by:
		user_ids.append(requested_by)

	manager_users = frappe.get_all(
		"Has Role",
		filters={"role": ["in", ["Sales Manager", "System Manager"]]},
		pluck="parent",
	)
	user_ids.extend(manager_users or [])

	enabled = []
	seen = set()
	for user_id in user_ids:
		if user_id in seen:
			continue
		seen.add(user_id)
		try:
			user_doc = frappe.get_doc("User", user_id)
			if user_doc.enabled:
				enabled.append(user_doc.name)
		except Exception:
			continue

	return enabled


def _get_queue_failure_recipients(requested_by=None):
	recipients = set()

	for user_id in _get_queue_failure_user_ids(requested_by):
		try:
			email = frappe.db.get_value("User", user_id, "email")
			if email:
				recipients.add(email)
		except Exception:
			continue

	return list(recipients)


QUEUE_FAILURE_EVENT = "klik_pos_invoice_queue_failed"


def _notify_queue_failure(invoice_doc, requested_by, error_message):
	# Push to the till first. Background submission returns HTTP 200 the moment the invoice
	# is queued, so without this the cashier sees a completed sale and only the Notification
	# Log and email below record that it never posted - neither of which is in front of
	# someone standing at a counter. after_commit so the alert cannot precede the failure
	# actually being recorded on the invoice.
	payload = {
		"invoice_name": invoice_doc.name,
		"customer": invoice_doc.customer_name or invoice_doc.customer,
		"error": _truncate_queue_error(error_message),
	}

	# Managers as well as the cashier: a supervisor should not learn about a failed sale from
	# an email minutes later. No noise risk - a realtime event only reaches a subscribed
	# client, so a manager who is not in the POS never sees it.
	for user_id in _get_queue_failure_user_ids(requested_by or frappe.session.user):
		try:
			frappe.publish_realtime(
				event=QUEUE_FAILURE_EVENT,
				message=payload,
				user=user_id,
				after_commit=True,
			)
		except Exception:
			# Never let the alert channel take down the record-keeping below.
			frappe.log_error(
				frappe.get_traceback(),
				f"Queue failure realtime publish failed for {invoice_doc.name} to {user_id}",
			)

	subject = f"POS invoice queue failed: {invoice_doc.name}"
	body = (
		f"Invoice <b>{invoice_doc.name}</b> failed in the background queue."
		f"<br><br><b>Customer:</b> {invoice_doc.customer_name or invoice_doc.customer}"
		f"<br><b>Error:</b> {_truncate_queue_error(error_message)}"
	)

	try:
		notification = frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": subject,
				"email_content": body,
				"for_user": requested_by or frappe.session.user,
				"type": "Alert",
			}
		)
		notification.insert(ignore_permissions=True)
	except Exception:
		pass

	recipients = _get_queue_failure_recipients(requested_by or frappe.session.user)
	if recipients:
		try:
			frappe.sendmail(recipients=recipients, subject=subject, message=body)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Failed to send queue failure alert for {invoice_doc.name}")


def _mark_invoice_queued(doc, requested_by=None):
	_update_queue_fields(doc, QUEUE_STATUSES["queued"], attempts=0)
	# Preserve held state if this queue action came from a held draft invoice.
	_apply_klik_invoice_flags(doc, is_submitted=False)
	if hasattr(doc, "queue_error"):
		doc.queue_error = ""
	if hasattr(doc, "queue_last_attempt_at"):
		doc.queue_last_attempt_at = None
	# owner is deliberately left alone: Frappe 16 treats it as set-once, and Frappe sets
	# it to the session user on insert anyway. requested_by stays for the callers' sake.


def _finalize_submitted_invoice(doc, amount_paid, mode_of_payment, business_type, customer):
	payment_entry = None
	should_create_payment_entry = False

	if business_type == "B2B":
		should_create_payment_entry = True
	elif business_type == "B2B & B2C":
		global _cached_customer_data
		if customer not in _cached_customer_data:
			_cached_customer_data[customer] = frappe.get_doc("Customer", customer)

		customer_doc = _cached_customer_data[customer]
		if customer_doc.customer_type == "Company":
			should_create_payment_entry = True

	if should_create_payment_entry and mode_of_payment and amount_paid > 0:
		try:
			payment_entry = create_payment_entry(doc, mode_of_payment, amount_paid)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Payment Entry Error for {doc.name}")
			payment_entry = None

	return payment_entry


def _get_payment_methods_from_invoice(doc):
	payment_methods = []
	for payment in getattr(doc, "payments", []) or []:
		payment_methods.append(
			{
				"method": payment.mode_of_payment,
				"amount": flt(payment.amount or 0),
			}
		)
	return payment_methods


def _get_invoice_response_summary(doc):
	return {
		"name": doc.name,
		"doctype": doc.doctype,
		"customer": doc.customer,
		"customer_name": doc.customer_name,
		"posting_date": doc.posting_date,
		"base_grand_total": doc.base_grand_total,
		"grand_total": doc.grand_total,
		"rounded_total": doc.rounded_total,
		"paid_amount": doc.paid_amount,
		"outstanding_amount": doc.outstanding_amount,
		"currency": doc.currency,
		"currency_symbol": frappe.db.get_value("Currency", doc.currency, "symbol") or doc.currency,
		"status": doc.status,
		"is_pos": doc.is_pos,
		"company": doc.company,
		"loyalty": get_invoice_loyalty_summary(doc),
	}


class PartialPaymentValidationError(ValidationError):
	pass


def get_current_pos_opening_entry():
	"""
	The shift the caller works in: the one they last opened or joined, while it is Open; else
	their own Open shift (one opened before klik remembered it); none after Switch profile.
	Returns the opening entry name or None if not found.
	"""
	try:
		from klik_pos.api.shift import JOINED_SHIFT_KEY, LEFT_SHIFT, joined_shift

		user = frappe.session.user
		if frappe.defaults.get_user_default(JOINED_SHIFT_KEY, user) == LEFT_SHIFT:
			return None
		entry = joined_shift(user)
		if entry:
			return entry

		opening_entries = frappe.get_all(
			"POS Opening Entry",
			filters={"user": user, "docstatus": 1, "status": "Open"},
			fields=["name"],
			order_by="creation desc",
			limit_page_length=1,
		)
		return opening_entries[0].name if opening_entries else None
	except Exception as e:
		frappe.log_error(f"Error getting current POS opening entry: {e!s}")
		return None


def _can_view_sales_dashboard(user_roles):
	"""Whether these roles may open the Sales Dashboard.

	DASHBOARD_ROLES is imported from api.user rather than restated here: that module
	serves the same list to the SPA for the nav gate, and the two drifting apart is what
	produced a dashboard tab that opened onto a query refusing to fill it.

	A plain string comparison, never a Role lookup, so "Express Admin" costs nothing on a
	klik_pos install without erpnext_express - no such role exists there, so it matches
	nobody and every other role behaves exactly as before.
	"""
	from klik_pos.api.user import DASHBOARD_ROLES

	return bool(set(DASHBOARD_ROLES) & set(user_roles or []))


def _profile_allows_other_cashiers(pos_doc):
	"""Whether this till lets its users read invoices they did not ring.

	A property of the POS Profile rather than of the person: with it off, everyone on the
	till is held to their own invoices in Invoice History - except managers
	(INVOICE_MANAGER_ROLES), who see the whole company regardless. Missing
	profile or missing field reads as off, so a site that has not migrated yet keeps the
	behaviour it has today rather than silently opening up.
	"""
	if not pos_doc:
		return False
	return bool(getattr(pos_doc, "custom_allow_viewing_other_cashiers", 0))


def _tills_sharing_warehouse(pos_doc):
	"""The tills (POS Profiles) of pos_doc's company selling from its warehouse, itself included.

	The warehouse is the shop. Allparts runs a sales till, a floor managers' till and an
	admin till on one warehouse: tills sharing it see each other's work when they allow it;
	a till on another warehouse - another branch of the same company - does not.
	"""
	name = getattr(pos_doc, "name", None)
	# The till's own warehouse, not one picked for this request: the shop does not move.
	warehouse = (frappe.db.get_value("POS Profile", name, "warehouse") if name else None) or getattr(
		pos_doc, "warehouse", None
	)
	company = getattr(pos_doc, "company", None)
	if not warehouse or not company:
		return [name] if name else []
	tills = frappe.get_all("POS Profile", filters={"company": company, "warehouse": warehouse}, pluck="name")
	if name and name not in tills:
		tills.append(name)
	return tills


def _is_invoice_manager(user=None):
	from klik_pos.api.user import INVOICE_MANAGER_ROLES

	return bool(INVOICE_MANAGER_ROLES & set(frappe.get_roles(user or frappe.session.user)))


def _may_read_row(owner, company, user, pos_doc, docstatus=1, pos_profile=None, shared_tills=None):
	"""Whether `user` may open a row owned by `owner`: their own, or - where `pos_doc` lets its
	users read each other's - another cashier's rung on a till selling from the same warehouse.

	Drafts too: a till sharing the warehouse finishing another's draft sells from the same
	stock. An invoice rung on no till is nobody else's to open here. `shared_tills` saves the
	lookup when a whole list is checked."""
	if owner == user:
		return True
	if not pos_doc or not company or company != getattr(pos_doc, "company", None):
		return False
	if _is_invoice_manager(user):
		return True
	if not _profile_allows_other_cashiers(pos_doc):
		return False
	tills = shared_tills if shared_tills is not None else _tills_sharing_warehouse(pos_doc)
	return bool(pos_profile) and pos_profile in tills


def _may_read_invoice(invoice):
	"""Whether the caller may open this invoice in the POS: their own, or another cashier's
	rung on a till sharing their till's warehouse when that till lets its users read each
	other's - what the Invoice History list shows, and what held orders follow. Managers
	(INVOICE_MANAGER_ROLES) open any invoice of the till's company; with no till resolvable,
	only their own."""
	try:
		pos_doc = get_current_pos_profile()
	except Exception:
		pos_doc = None
	return _may_read_row(
		invoice.owner,
		invoice.company,
		frappe.session.user,
		pos_doc,
		docstatus=invoice.docstatus,
		pos_profile=invoice.pos_profile,
	)


@frappe.whitelist()
def get_sales_invoices(
	limit=100,
	start=0,
	search="",
	skip_opening_entry_filter=False,
	cashier_name=None,
	submitted_only=False,
	surface="",
	from_date=None,
	to_date=None,
):
	"""
	Get sales invoices with proper filtering based on user role and POS opening entry.

	Args:
		skip_opening_entry_filter: If True, skip filtering by opening entry (for Invoice History page)
		cashier_name: Filter by cashier name (full name). If provided, only returns invoices for that cashier.
		submitted_only: If True, only return submitted invoices (docstatus=1). Use for Sales Dashboard; excludes Draft and Cancelled.
		from_date, to_date: Optional posting_date bounds, both inclusive. Empty means unbounded.
		surface: Which screen is asking. Four screens share this endpoint and they do not
			want the same scoping, so they say which they are rather than having it
			guessed from the other arguments:

			"dashboard" - the Sales Dashboard. Restricts nothing: no opening entry, no POS
				profile, no owner. The dashboard is an overview of the business, so the
				gate is who may open it, checked here rather than only in the SPA.
			"history"   - Invoice History, and "customer" - a customer's invoice list.
				Cross-cashier reading is decided by the till's
				POS Profile (custom_allow_viewing_other_cashiers), not by role. With it
				off the owner filter is applied in SQL; previously the restriction existed
				only because the page sent its own name as cashier_name, which anyone
				could omit. With it on: one's own invoices, and other cashiers' rung on a
				till selling from this till's warehouse - what _may_read_row opens.
				(Non-managers are further held to those tills by the pos_profile
				condition below.)
				The customer list follows the same rule because an invoice the caller may
				not open (get_invoice_details) must not be listed either.
			""          - Closing Shift. Untouched: a shift legitimately spans cashiers on a
				shared till.
	"""
	try:
		if isinstance(skip_opening_entry_filter, str):
			skip_opening_entry_filter = skip_opening_entry_filter.lower() in ("true", "1", "yes")
		if isinstance(submitted_only, str):
			submitted_only = submitted_only.lower() in ("true", "1", "yes")

		limit = int(limit) if limit else 100
		start = int(start) if start else 0

		cashier_user_ids = None
		if cashier_name and cashier_name != "all":
			cashier_user_ids = _get_user_ids_by_full_name(cashier_name)
			if not cashier_user_ids:
				return {"success": True, "data": [], "total_count": 0}

		pos_doc = None
		try:
			pos_doc = get_current_pos_profile()
			current_pos_profile = getattr(pos_doc, "name", None)
		except Exception:
			current_pos_profile = None

		current_opening_entry = get_current_pos_opening_entry()
		user_roles = frappe.get_roles()
		is_admin_user = "Administrator" in user_roles or "System Manager" in user_roles

		if surface == "dashboard" and not _can_view_sales_dashboard(user_roles):
			return {"success": False, "error": _("Not permitted to view the Sales Dashboard")}

		# Every surface but the three named screens gets Closing Shift's scope (the whole
		# shift, every cashier), so every one of them is held to the closing rule.
		if surface not in ("history", "customer", "dashboard") and current_pos_profile:
			from klik_pos.api.shift import may_close_on_till

			if not may_close_on_till(current_pos_profile):
				return {
					"success": False,
					"error": _("Closing is turned off on {0}. A manager closes this till's shift.").format(
						current_pos_profile
					),
				}

		sales_invoice_meta = frappe.get_meta("Sales Invoice")
		has_zatca_status = any(df.fieldname == "custom_zatca_submit_status" for df in sales_invoice_meta.fields)
		has_custom_is_held = any(df.fieldname == "custom_is_held" for df in sales_invoice_meta.fields)
		has_custom_is_submitted = any(df.fieldname == "custom_is_submitted" for df in sales_invoice_meta.fields)
		has_custom_is_created_from_klik = any(
			df.fieldname == "custom_is_created_from_klik" for df in sales_invoice_meta.fields
		)
		has_walkin_name = any(df.fieldname == "custom_walkin_customer_name" for df in sales_invoice_meta.fields)

		select_fields = """name, docstatus, posting_date, posting_time, owner, customer, customer_name,
			base_grand_total, base_rounded_total, status, discount_amount,
			total_taxes_and_charges, custom_pos_opening_entry, queue_status,
			queue_error, queue_attempts, queue_last_attempt_at, pos_profile, company, currency, custom_is_printed"""
		if has_zatca_status:
			select_fields += ", custom_zatca_submit_status"
		if has_custom_is_held:
			select_fields += ", custom_is_held"
		if has_custom_is_submitted:
			select_fields += ", custom_is_submitted"
		if has_custom_is_created_from_klik:
			select_fields += ", custom_is_created_from_klik"
		if has_walkin_name:
			select_fields += ", custom_walkin_customer_name"

		conditions = []
		params = []

		shared_tills = (
			_tills_sharing_warehouse(pos_doc) if pos_doc and _profile_allows_other_cashiers(pos_doc) else None
		)
		manager_sees_company = surface in ("history", "customer") and pos_doc and _is_invoice_manager()
		if manager_sees_company:
			# One's own, as everyone; and every invoice of the till's company - other tills',
			# and desk ones rung on none.
			conditions.append("(si.owner = %s OR si.company = %s)")
			params.extend([frappe.session.user, getattr(pos_doc, "company", None)])
		elif surface in ("history", "customer"):
			if not shared_tills:
				conditions.append("si.owner = %s")
				params.append(frappe.session.user)
			else:
				# Exactly what _may_read_row opens: one's own, and others' rung on a till
				# selling from this till's warehouse.
				placeholders = ", ".join(["%s"] * len(shared_tills))
				conditions.append(f"(si.owner = %s OR (si.company = %s AND si.pos_profile IN ({placeholders})))")
				params.extend([frappe.session.user, getattr(pos_doc, "company", None), *shared_tills])

		if surface != "dashboard" and not skip_opening_entry_filter:
			if is_admin_user:
				conditions.append("si.custom_pos_opening_entry != ''")
			elif current_opening_entry:
				conditions.append("si.custom_pos_opening_entry = %s")
				params.append(current_opening_entry)
			else:
				conditions.append("si.custom_pos_opening_entry != ''")

		if submitted_only:
			conditions.append("si.docstatus = 1")

		if cashier_user_ids:
			if len(cashier_user_ids) == 1:
				conditions.append("si.owner = %s")
				params.append(cashier_user_ids[0])
			else:
				placeholders = ", ".join(["%s"] * len(cashier_user_ids))
				conditions.append(f"si.owner IN ({placeholders})")
				params.extend(cashier_user_ids)

		if surface != "dashboard" and current_pos_profile and not is_admin_user and not manager_sees_company:
			if surface in ("history", "customer") and shared_tills:
				# Not only this till: every till selling from its warehouse.
				placeholders = ", ".join(["%s"] * len(shared_tills))
				conditions.append(f"si.pos_profile IN ({placeholders})")
				params.extend(shared_tills)
			else:
				conditions.append("si.pos_profile = %s")
				params.append(current_pos_profile)

		if search and search.strip():
			search_term = f"%{search.strip()}%"
			searched = ["si.name", "si.customer_name", "si.customer"]
			# Walk-in sales all sit under one customer; the buyer's own name tells them apart.
			if has_walkin_name:
				searched.append("si.custom_walkin_customer_name")
			conditions.append("(" + " OR ".join(f"{col} LIKE %s" for col in searched) + ")")
			params.extend([search_term] * len(searched))

		if from_date:
			conditions.append("si.posting_date >= %s")
			params.append(getdate(from_date))
		if to_date:
			conditions.append("si.posting_date <= %s")
			params.append(getdate(to_date))

		where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

		count_sql = apply_sql_permissions(
			f"SELECT COUNT(*) as total FROM `tabSales Invoice` si {where_clause}"
		)
		count_result = frappe.db.sql(count_sql, tuple(params), as_dict=True)
		total_count = count_result[0]["total"] if count_result else 0

		main_sql = apply_sql_permissions(f"""
			SELECT {select_fields}
			FROM `tabSales Invoice` si
			{where_clause}
			ORDER BY si.modified DESC
			LIMIT %s OFFSET %s
		""")
		invoices = frappe.db.sql(main_sql, (*params, limit, start), as_dict=True)

		if not invoices:
			return {"success": True, "data": [], "total_count": total_count}

		invoice_names = [inv.name for inv in invoices]
		user_ids = list(set([inv.owner for inv in invoices]))

		cashier_names_map = _batch_fetch_cashier_names(user_ids)
		payment_methods_map = _batch_fetch_payment_methods(invoice_names)
		items_map = _batch_fetch_items(invoice_names)

		for inv in invoices:
			inv["can_open"] = _may_read_row(
				inv.owner,
				inv.company,
				frappe.session.user,
				pos_doc,
				docstatus=inv.docstatus,
				pos_profile=inv.pos_profile,
				shared_tills=shared_tills or [],
			)

		_process_invoices(invoices, cashier_names_map, payment_methods_map, items_map)

		return {"success": True, "data": invoices, "total_count": total_count}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Error fetching sales invoices")
		return {"success": False, "error": str(e)}

def _get_user_ids_by_full_name(full_name):
	"""Get user IDs (emails) that match the given full name."""
	try:
		users = frappe.get_all(
			"User",
			filters={"full_name": full_name, "enabled": 1},
			fields=["name"],
		)
		return [user.name for user in users] if users else []
	except Exception as e:
		frappe.logger().error(f"Error getting user IDs by full name '{full_name}': {e}")
		return []


def _sql_in_clause(values):
	"""Build a parameterized SQL IN clause for dynamic value lists."""
	values = list(values or [])
	return ", ".join(["%s"] * len(values)), tuple(values)


def _batch_fetch_cashier_names(user_ids):
	"""Batch fetch cashier names for given user IDs."""
	if not user_ids:
		return {}

	placeholders, params = _sql_in_clause(user_ids)
	cashier_query = """
		SELECT name, full_name
		FROM `tabUser`
		WHERE name IN ({})
	""".format(placeholders)
	cashier_results = frappe.db.sql(cashier_query, params, as_dict=True)
	return {user.name: user.full_name or user.name for user in cashier_results}


def _batch_fetch_payment_methods(invoice_names):
	"""Batch fetch payment methods for given invoices."""
	if not invoice_names:
		return {}

	placeholders, params = _sql_in_clause(invoice_names)
	payment_query = """
		SELECT sip.parent, sip.mode_of_payment, sip.amount, sip.account, sip.type,
			si.change_amount, si.account_for_change_amount
		FROM `tabSales Invoice Payment` sip
		INNER JOIN `tabSales Invoice` si ON si.name = sip.parent
		WHERE sip.parent IN ({})
		ORDER BY sip.parent, sip.idx
	""".format(placeholders)
	payment_results = frappe.db.sql(payment_query, params, as_dict=True)

	# Group by parent invoice. The row keeps what was tendered; the change handed back is
	# named on the row it left through - the Cash row first, else the row paying into
	# account_for_change_amount (payment._change_given_by_mode, same rule) - so the Closing
	# Shift cards count what stayed in the drawer.
	payment_methods_map = {}
	by_invoice = {}
	for payment in payment_results:
		by_invoice.setdefault(payment.parent, []).append(payment)
	for parent, payments in by_invoice.items():
		change = flt(payments[0].change_amount)
		change_row = None
		if change > 0:
			candidates = [
				p for p in payments if p.type == "Cash" or (p.account and p.account == p.account_for_change_amount)
			]
			candidates.sort(key=lambda p: (p.type != "Cash", p.account != p.account_for_change_amount))
			change_row = candidates[0] if candidates else None
		rows = []
		for payment in payments:
			row = {"mode_of_payment": payment.mode_of_payment, "amount": payment.amount}
			if payment is change_row:
				row["change_amount"] = change
			rows.append(row)
		payment_methods_map[parent] = rows

	from klik_pos.api.payment_rows import advance_payment_rows, merge_payment_rows

	advances = advance_payment_rows(invoice_names)
	for name, rows in advances.items():
		payment_methods_map[name] = merge_payment_rows(payment_methods_map.get(name, []), rows)

	return payment_methods_map


def _batch_fetch_items(invoice_names):
	"""Batch fetch items for given invoices."""
	if not invoice_names:
		return {}

	placeholders, params = _sql_in_clause(invoice_names)
	items_query = """
		SELECT parent, item_code, qty, rate, amount
		FROM `tabSales Invoice Item`
		WHERE parent IN ({})
	""".format(placeholders)
	items_results = frappe.db.sql(items_query, params, as_dict=True)

	# Group by parent invoice
	items_map = {}
	for item in items_results:
		if item.parent not in items_map:
			items_map[item.parent] = []
		items_map[item.parent].append(
			{
				"item_code": item.item_code,
				"qty": item.qty,
				"rate": item.rate,
				"amount": item.amount,
				"quantity": item.qty,
			}
		)

	return items_map


def _process_invoices(invoices, cashier_names_map, payment_methods_map, items_map):
	"""Process and enrich invoices with related data."""
	for inv in invoices:
		# Set cashier name
		inv["cashier_name"] = cashier_names_map.get(inv.owner, inv.owner)

		# Format posting_time
		if inv.get("posting_time"):
			if hasattr(inv["posting_time"], "total_seconds"):
				total_seconds = int(inv["posting_time"].total_seconds())
				hours = total_seconds // 3600
				minutes = (total_seconds % 3600) // 60
				seconds = total_seconds % 60
				inv["posting_time"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
			else:
				inv["posting_time"] = str(inv["posting_time"])

		# Set payment methods
		payment_methods = payment_methods_map.get(inv.name, [])
		inv["payment_methods"] = payment_methods

		# Backward-compatible mode_of_payment label, shared with the detail page and the
		# return picker so the three surfaces cannot disagree.
		inv["mode_of_payment"] = mode_label(payment_methods)

		# Set items and calculate return data
		items = items_map.get(inv.name, [])

		# Only calculate return data for Credit Note Issued invoices
		if inv.get("status") == "Credit Note Issued":
			_calculate_return_quantities(inv, items)
		else:
			for item in items:
				item["returned_qty"] = 0
				item["available_qty"] = item["qty"]

		inv["items"] = items


def _calculate_return_quantities(invoice, items):
	"""Calculate return quantities for credit note invoices."""
	item_codes = [item["item_code"] for item in items]
	if not item_codes:
		return

	item_placeholders, item_params = _sql_in_clause(item_codes)
	returns_query = """
		SELECT sii.item_code, COALESCE(SUM(ABS(sii.qty)), 0) as total_returned_qty
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
		WHERE si.is_return = 1
		  AND si.return_against = %s
		  AND sii.item_code IN ({})
		  AND si.docstatus = 1
		  AND si.customer = %s
		GROUP BY sii.item_code
	""".format(item_placeholders)

	returns_data = frappe.db.sql(
		returns_query, (invoice.name, *item_params, invoice.customer), as_dict=True
	)
	returned_qty_map = {row.item_code: row.total_returned_qty for row in returns_data}

	# Update items with return data
	for item in items:
		returned_qty_value = returned_qty_map.get(item["item_code"], 0)
		item["returned_qty"] = round(float(returned_qty_value), 6)
		item["available_qty"] = round(item["qty"] - returned_qty_value, 6)


@frappe.whitelist()
def get_invoice_details(invoice_id):
	"""
	Main function to fetch complete invoice details.

	Read permission first, then the till's rule for other cashiers' invoices - the same one
	Invoice History lists by.
	"""
	try:
		invoice = frappe.get_doc("Sales Invoice", invoice_id)
		invoice.check_permission("read")
		if not _may_read_invoice(invoice):
			raise frappe.PermissionError(_("This invoice was rung by another cashier."))
		invoice_data = invoice.as_dict()

		# Get items with return data
		items = _get_invoice_items_with_returns(invoice_id, invoice.customer)

		# Get address and customer information
		address_data = _get_address_and_customer_info(invoice)

		# Format posting time
		if invoice_data.get("posting_time"):
			if hasattr(invoice_data["posting_time"], "total_seconds"):
				total_seconds = int(invoice_data["posting_time"].total_seconds())
				hours = total_seconds // 3600
				minutes = (total_seconds % 3600) // 60
				seconds = total_seconds % 60
				invoice_data["posting_time"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
			else:
				invoice_data["posting_time"] = str(invoice_data["posting_time"])

		# Get cashier full name
		cashier_name = frappe.db.get_value(
			"User", invoice_data.get("owner"), "full_name"
		) or invoice_data.get("owner")
		invoice_data["cashier_name"] = cashier_name

		# Ceiling for a cash refund on this invoice. 0 for a credit sale, so the return dialog can
		# show a credit note instead of offering money back. Derived here so the UI never has to
		# re-implement the rule.
		# The return inherits is_pos from this invoice, so it doubles as the return_doc probe.
		invoice_data["refundable_cash"] = (
			_get_refundable_cash(invoice, invoice) if not invoice.is_return else 0.0
		)

		# A Shipping Rule's courier fee, for the return dialog to offer as a line the cashier
		# ticks - read the same way the return picker reads it.
		reversed_by = _reversed_fixed_charges([invoice.name]).get(invoice.name, {})
		invoice_data["fixed_charges"] = [
			{**charge, "reversed_by": reversed_by.get(charge["account_head"])}
			for charge in _fixed_charges_for([invoice.name]).get(invoice.name, [])
		]

		# Derived the same way the list endpoint derives it (_process_invoices), so the detail
		# page and Invoice History cannot disagree about how an invoice was paid. Without this
		# the detail page saw no payment field at all and fell back to displaying "Cash",
		# which reads as a fact rather than an absence — an M-Pesa sale showed as Cash.
		from klik_pos.api.payment_rows import advance_payment_rows, merge_payment_rows

		payments = merge_payment_rows(
			[{"mode_of_payment": p.mode_of_payment, "amount": flt(p.amount or 0)} for p in (getattr(invoice, "payments", []) or [])],
			advance_payment_rows([invoice.name]).get(invoice.name, []),
		)
		invoice_data["payment_methods"] = payments
		invoice_data["mode_of_payment"] = mode_label(payments)
		from klik_pos.api.payment_rows import payment_details

		invoice_data["payment_details"] = payment_details(invoice)

		return {
			"success": True,
			"data": {
				**invoice_data,
				"items": items,
				"loyalty": get_invoice_loyalty_summary(invoice),
				**address_data,
			},
		}

	except frappe.PermissionError as e:
		# A refusal is an answer, not a fault: no Error Log, and no leftover message from
		# the lookups on the way here.
		frappe.local.message_log = []
		return {
			"success": False,
			"code": "forbidden",
			"error": str(e) or _("You are not permitted to open invoice {0}.").format(invoice_id),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), f"Error fetching invoice {invoice_id}")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def mark_invoice_as_printed(invoice_name):
	try:
		frappe.db.sql(
			"UPDATE `tabSales Invoice` SET custom_is_printed = 1 WHERE name = %s",
			(invoice_name,)
		)
		return {"success": True}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), f"Error marking invoice {invoice_name} as printed")
		return {"success": False, "error": str(e)}


def _build_preview_doc(data):
	"""The checkout's invoice, built but not saved, and the Loss of Sale split it made.

	Shared by validate_checkout_invoice and the supervisor's price authorisation, so a
	supervisor authorises exactly the cart the preview showed.
	"""
	if isinstance(data, str):
		data = json.loads(data)
	(
		customer,
		items,
		amount_paid,
		sales_and_tax_charges,
		mode_of_payment,
		business_type,
		roundoff_amount,
		delivery_charge,
		delivery_personnel,
		is_credit_sale,
		allow_partial_payment,
		due_date,
		salesperson,
		tax_id,
		enable_background_submission,
		loyalty_redemption,
	) = parse_invoice_data(data)

	# Before payment: shorten lines to the stock there is and tell the till what changed.
	los_adjustments = split_cart_items(items, _get_active_pos_profile())

	preview_doc = build_sales_invoice_doc(
		customer,
		items,
		amount_paid,
		sales_and_tax_charges,
		mode_of_payment,
		business_type,
		roundoff_amount,
		delivery_charge,
		include_payments=False,
		delivery_personnel=delivery_personnel,
		is_credit_sale=is_credit_sale,
		due_date=due_date,
		salesperson=salesperson,
		tax_id=tax_id,
		create_batch_and_serial_bundle=False,
		enable_background_submission=enable_background_submission,
		loyalty_redemption=loyalty_redemption,
		walkin_name=data.get("walkin_name"),
		walkin_phone=data.get("walkin_phone"),
		extra_fields=_parse_extra_fields(data),
		order_discount_amount=flt(data.get("orderDiscountAmount") or 0),
		shipping_rule=data.get("shipping_rule") or None,
		selling_price_list=data.get("selling_price_list") or None,
	)
	return preview_doc, los_adjustments


@frappe.whitelist()
def validate_checkout_invoice(data):
	"""
	Pre-validate invoice payload at checkout time without creating any document.
	This catches batch/serial and item-account issues early before payment submission.
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)
		preview_doc, los_adjustments = _build_preview_doc(data)
		# A resumed held order: its manager's price approval covers the preview, as at checkout.
		held_order_id = data.get("held_order_id") if isinstance(data, dict) else None
		if held_order_id and preview_doc.meta.has_field("powerpack_source_order"):
			preview_doc.powerpack_source_order = held_order_id

		validate_required_salesperson(preview_doc)

		_validate_reserved_stock_for_items(preview_doc)

		shipping_account = (
			frappe.db.get_value("Shipping Rule", preview_doc.shipping_rule, "account")
			if preview_doc.get("shipping_rule")
			else None
		)
		tax_breakdown = []
		shipping_amount = 0.0
		for tax in preview_doc.get("taxes") or []:
			# The till shows a shipping charge on its own line, not folded into "Tax".
			is_shipping = bool(
				shipping_account
				and tax.charge_type == "Actual"
				and tax.account_head == shipping_account
			)
			if is_shipping:
				shipping_amount += flt(tax.tax_amount or 0)
			tax_breakdown.append(
				{
					"description": tax.description,
					"account_head": tax.account_head,
					"charge_type": tax.charge_type,
					"rate": flt(tax.rate or 0),
					"tax_amount": flt(tax.tax_amount or 0),
					"total": flt(tax.total or 0),
					"included_in_print_rate": int(tax.included_in_print_rate or 0),
					"is_shipping": int(is_shipping),
				}
			)

		result = {
			"success": True,
			"message": "Checkout validation passed",
			"los_adjustments": los_adjustments,
			"tax_preview": {
				"tax_breakdown": tax_breakdown,
				# The receipt renders lines from the cart and totals from this document.
				# Hand the frontend the lines this document actually priced so it can tell
				# when the two disagree instead of printing a receipt that does not add up.
				"items": [
					{
						"item_code": row.item_code,
						"qty": flt(row.qty or 0),
						"rate": flt(row.rate or 0),
						"amount": flt(row.amount or 0),
					}
					for row in preview_doc.get("items") or []
				],
				"net_total": flt(preview_doc.net_total or 0),
				# The order discount, applied on the grand total: net_total already has its
				# pre-tax share taken out, so the till needs it to show Subtotal - Discount.
				"discount_amount": flt(preview_doc.discount_amount or 0),
				"total_taxes_and_charges": flt(preview_doc.total_taxes_and_charges or 0),
				"grand_total": flt(preview_doc.grand_total or 0),
				"rounded_total": flt(preview_doc.rounded_total or 0),
				"disable_rounded_total": int(preview_doc.disable_rounded_total or 0),
				"shipping_amount": flt(shipping_amount),
				"total_net_weight": flt(preview_doc.get("total_net_weight") or 0),
				# Lines below their price list's minimum, on a till that enforces it.
				"price_floor": _price_floor_report(preview_doc),
			},
		}

		return result

	except Exception as e:
		return {"success": False, "message": str(e)}
	

def _get_invoice_items_with_returns(invoice_id, customer):
	"""
	Fetch invoice items and calculate returned/available quantities.
	"""
	# Batch fetch all items for this invoice
	items_query = """
		SELECT name, item_code, item_name, qty, rate, amount, description, uom,
			price_list_rate, discount_amount, discount_percentage
		FROM `tabSales Invoice Item`
		WHERE parent = %s
	"""
	items_data = frappe.db.sql(items_query, (invoice_id,), as_dict=True)

	# Batch fetch return quantities for all items at once
	item_codes = [item.item_code for item in items_data]
	returned_qty_map = {}

	if item_codes:
		item_placeholders, item_params = _sql_in_clause(item_codes)
		returns_query = """
			SELECT sii.item_code, COALESCE(SUM(ABS(sii.qty)), 0) as total_returned_qty
			FROM `tabSales Invoice` si
			JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
			WHERE si.is_return = 1
			  AND si.return_against = %s
			  AND sii.item_code IN ({})
			  AND si.docstatus = 1
			  AND si.customer = %s
			GROUP BY sii.item_code
		""".format(item_placeholders)

		returns_data = frappe.db.sql(
			returns_query, (invoice_id, *item_params, customer), as_dict=True
		)
		returned_qty_map = {row.item_code: row.total_returned_qty for row in returns_data}

	# Build items list with return data
	items = []
	for item in items_data:
		returned_qty_value = returned_qty_map.get(item.item_code, 0)
		available_qty = round(item.qty - returned_qty_value, 6)

		items.append(
			{
				"name": item.name,
				"item_code": item.item_code,
				"item_name": item.item_name,
				"qty": item.qty,
				"rate": item.rate,
				"price_list_rate": item.price_list_rate,
				"discount_amount": item.discount_amount,
				"discount_percentage": item.discount_percentage,
				"amount": item.amount,
				"description": item.description,
				"uom": item.uom,
				"returned_qty": returned_qty_value,
				"available_qty": available_qty,
			}
		)

	return items


def _get_address_and_customer_info(invoice):
	"""
	Fetch company address, customer address, and customer contact information.
	"""
	# Get company address
	company_address_doc = None
	if invoice.company_address:
		company_address_doc = frappe.get_doc("Address", invoice.company_address).as_dict()

	# Get customer address
	customer_address_doc = None
	if invoice.customer_address:
		customer_address_doc = frappe.get_doc("Address", invoice.customer_address).as_dict()
	else:
		primary_address = frappe.db.get_value(
			"Dynamic Link",
			{
				"link_doctype": "Customer",
				"link_name": invoice.customer,
				"parenttype": "Address",
			},
			"parent",
		)
		if primary_address:
			customer_address_doc = frappe.get_doc("Address", primary_address).as_dict()

	# Get customer contact information
	customer_email = ""
	customer_mobile_no = ""
	customer_address_line1 = ""
	customer_city = ""
	customer_state = ""
	customer_pincode = ""
	customer_country = ""
	customer_is_walkin = 0

	if invoice.customer:
		customer_doc = frappe.get_doc("Customer", invoice.customer)
		customer_email = customer_doc.email_id or ""
		customer_mobile_no = customer_doc.mobile_no or ""
		customer_is_walkin = customer_doc.custom_is_walkin

		# Extract address fields
		if customer_address_doc:
			customer_address_line1 = customer_address_doc.get("address_line1", "")
			customer_city = customer_address_doc.get("city", "")
			customer_state = customer_address_doc.get("state", "")
			customer_pincode = customer_address_doc.get("pincode", "")
			customer_country = customer_address_doc.get("country", "")

	return {
		"company_address_doc": company_address_doc,
		"customer_address_doc": customer_address_doc,
		"customer_email": customer_email,
		"customer_mobile_no": customer_mobile_no,
		"customer_address_line1": customer_address_line1,
		"customer_city": customer_city,
		"customer_state": customer_state,
		"customer_pincode": customer_pincode,
		"customer_country": customer_country,
		"customer_is_walkin": customer_is_walkin,
	}


@frappe.whitelist()
def create_and_submit_invoice(data):
	return queue_sales_invoice(data)


@frappe.whitelist()
def queue_sales_invoice(data):
	return _retry_lock_race(_queue_sales_invoice, data)


def _refuse_crossing_credit_limit(doc):
	"""ERPNext checks the credit limit on submit. A queued sale is saved - its number taken -
	before the worker submits it, so check here, while the checkout still rolls back
	(Allparts INV-00224). Same conditions as SalesInvoice.check_credit_limit, with this
	sale's unpaid part added since it is not on the books yet."""
	from erpnext.selling.doctype.customer.customer import check_credit_limit

	if doc.get("is_return"):
		return
	bypass = frappe.db.get_value(
		"Customer Credit Limit",
		{"parent": doc.customer, "parenttype": "Customer", "company": doc.company},
		"bypass_credit_limit_check",
	)
	if not bypass and all(d.sales_order or d.delivery_note for d in doc.items):
		return
	try:
		check_credit_limit(doc.customer, doc.company, bypass, extra_amount=flt(doc.outstanding_amount))
	except ValidationError as error:
		# The till shows the server message, not the exception: replace ERPNext's. Take it
		# from the message log, which keeps its HTML; the exception text loses it when the
		# server's stdin is a terminal, and the user list then runs together.
		logged = frappe.local.message_log[-1] if frappe.local.message_log else None
		message = (logged.get("message") if isinstance(logged, dict) else None) or str(error)
		frappe.clear_last_message()
		frappe.throw(f"{message}<br>" + _("Hold the order instead, or take payment."))


def _queue_sales_invoice(data, source_order=None):
	"""queue_sales_invoice's body. source_order is set only by checkout_held_order: a
	client must not be able to name another order whose price approval it wants."""
	checkout_request_id = None
	state = _CheckoutState()
	try:
		import time

		start_time = time.time()

		if not data:
			frappe.throw("No data provided for invoice creation")
		if isinstance(data, str):
			data = json.loads(data)

		# Claim before any document work: a retried checkout must find the first call's
		# result here and create nothing.
		checkout_request_id = _normalize_checkout_request_id(data.get("checkout_request_id"))
		existing_checkout = _claim_checkout_request(checkout_request_id)
		if existing_checkout:
			return _checkout_request_response(existing_checkout)
		state.mark_claimed(checkout_request_id)

		(
			customer,
			items,
			amount_paid,
			sales_and_tax_charges,
			mode_of_payment,
			business_type,
			roundoff_amount,
			delivery_charge,
			delivery_personnel,
			is_credit_sale,
			allow_partial_payment,
			due_date,
			salesperson,
			tax_id,
			enable_background_submission,
			loyalty_redemption,
		) = parse_invoice_data(data)

		from klik_pos.api.pos_profile import validate_required_extra_fields
		validate_required_extra_fields(_parse_extra_fields(data))

		if not customer:
			frappe.throw("Customer is required")
		if not items or len(items) == 0:
			frappe.throw("At least one item is required")

		receipts = _picked_receipts(data)
		if not flt(amount_paid) and not loyalty_redemption and not data.get("customerCredit") and not receipts:
			# Nothing taken yet (a credit sale). The preview split the cart the cashier showed;
			# if stock has moved since, billing a different split would go unseen - refuse it.
			# A paid sale is not re-split at all: its money was taken against the preview.
			if split_cart_items(items, _get_active_pos_profile()):
				frappe.throw(_("Stock changed since checkout - review the cart."))

		doc = build_sales_invoice_doc(
			customer,
			items,
			amount_paid,
			sales_and_tax_charges,
			mode_of_payment,
			business_type,
			roundoff_amount,
			delivery_charge,
			include_payments=True,
			delivery_personnel=delivery_personnel,
			is_credit_sale=is_credit_sale,
			allow_partial_payment=allow_partial_payment,
			due_date=due_date,
			salesperson=salesperson,
			tax_id=tax_id,
			enable_background_submission=enable_background_submission,
			loyalty_redemption=loyalty_redemption,
			walkin_name=data.get("walkin_name"),
			walkin_phone=data.get("walkin_phone"),
			extra_fields=_parse_extra_fields(data),
			order_discount_amount=flt(data.get("orderDiscountAmount") or 0),
			shipping_rule=data.get("shipping_rule") or None,
			selling_price_list=data.get("selling_price_list") or None,
		)

		if source_order and doc.meta.has_field("powerpack_source_order"):
			doc.powerpack_source_order = source_order
		_apply_quote_link(doc, data)
		_apply_price_authorisation(doc, data)
		_apply_remarks(doc, data)

		validate_required_salesperson(doc)

		paid_credit = flt(amount_paid) + flt(getattr(doc, "loyalty_amount", 0))
		doc.base_paid_amount = paid_credit
		doc.paid_amount = paid_credit
		doc.outstanding_amount = max(flt(doc.grand_total) - paid_credit, 0)
		# Only claim stock when the site allows reservation. Left at 0 the invoice never
		# reserves, never validates against reservations, and has nothing to release —
		# while an invoice flagged before the switch was turned off still takes the
		# cancel path below, so nothing is stranded mid-flight.
		doc.reserve_stock = 1 if _stock_reservation_enabled() else 0
		_apply_klik_invoice_flags(doc, is_held=False, is_submitted=False)

		_validate_reserved_stock_for_items(doc)

		# Customer credit: every row checked before anything persists; marked on the
		# invoice once the receipts are on it (_reserve_checkout_credit), applied after submit.
		credit_rows = validate_allocations(doc, data.get("customerCredit") or [])

		if receipts:
			from klik_pos.api.mpesa import attach_mpesa_receipts

			# Picked at checkout, recorded only now: the click wrote nothing, so an abandoned
			# receipt checkout never drew a receipt number.
			attach_mpesa_receipts(doc, receipts)

		_reserve_checkout_credit(doc, credit_rows)

		if enable_background_submission:
			_refuse_crossing_credit_limit(doc)
			_mark_invoice_queued(doc, frappe.session.user)
			doc.save(ignore_permissions=True)
			_refuse_unapproved_price_breach(doc)
			# The document exists from here on, so the ledger must name it before anything
			# else can fail — otherwise a retry would not find it. This also arms the
			# savepoint that stops _abort_checkout from rolling the saved invoice away.
			state.mark_invoice_persisted(doc.name)

			try:
				_reserve_stock_for_queued_invoice(doc)
			except Exception as reserve_error:
				_raise_lock_race(reserve_error)
				_update_queue_fields(doc, QUEUE_STATUSES["failed"], error_message=str(reserve_error))
				doc.save(ignore_permissions=True)
				_update_checkout_request(
					checkout_request_id,
					status="Failed",
					invoice_name=doc.name,
					error_message=reserve_error,
				)
				return {
					"success": False,
					"message": str(reserve_error),
					"checkout_request_id": checkout_request_id,
					"invoice_name": doc.name,
					"invoice_id": doc.name,
				}

			if _needs_shift_check(doc):
				# A klik POS sale still needs exactly one Open shift on its till, opened
				# today, before it is queued - the worker skips this check when it
				# finishes the sale, so it must be run here or never.
				doc.validate_pos_opening_entry()

			frappe.enqueue(
				"klik_pos.api.sales_invoice.process_queued_sales_invoice",
				queue="long",
				enqueue_after_commit=True,
				invoice_name=doc.name,
				requested_by=frappe.session.user,
				customer_credit=credit_rows,
			)

			doc.save(ignore_permissions=True)

			if tax_id:
				doc.db_set("tax_id", tax_id)

			processing_time = time.time() - start_time
			frappe.logger().info(f"Invoice {doc.name} queued in {processing_time:.2f} seconds")

			return {
				"success": True,
				"checkout_request_id": checkout_request_id,
				"queue_status": doc.queue_status,
				"invoice_name": doc.name,
				"invoice_id": doc.name,
				"invoice": _get_invoice_response_summary(doc),
				"payment_entry": None,
				"customer_credit": {"applied": 0.0, "warning": None},
				"processing_time": round(processing_time, 2),
			}
		else:
			doc.insert(ignore_permissions=True)
			_refuse_unapproved_price_breach(doc)
			state.mark_invoice_persisted(doc.name)

			if tax_id:
				doc.db_set("tax_id", tax_id)

			# Payment-Entry-first M-Pesa, as the queue worker does it: the receipts become
			# advances before submit and are reconciled and consumed after.
			mpesa_allocation = mpesa_reconciliation = None
			if doc.get("custom_mpesa_reconciled_payments"):
				from klik_pos.api.mpesa import _allocate_receipts_before_submit

				mpesa_allocation = _allocate_receipts_before_submit(doc)

			_apply_klik_invoice_flags(doc, is_submitted=True)
			_enforce_submit_permission(doc)
			doc.submit()
			doc.reload()

			try:
				_cancel_sales_invoice_reservations(doc.name)
			except Exception as e:
				_raise_lock_race(e)
				frappe.log_error(
					frappe.get_traceback(),
					f"Failed to cancel reservations after submit for {doc.name}",
				)

			if doc.get("custom_mpesa_reconciled_payments"):
				from klik_pos.api.mpesa import _finalize_mpesa_reconciliation

				mpesa_reconciliation = _finalize_mpesa_reconciliation(doc, mpesa_allocation)

			_finalize_submitted_invoice(
				doc,
				flt(doc.paid_amount or 0),
				_get_payment_methods_from_invoice(doc),
				getattr(doc, "business_type", None),
				doc.customer,
			)

			credit_result = _apply_checkout_credit(doc, credit_rows)

			processing_time = time.time() - start_time
			frappe.logger().info(f"Invoice {doc.name} submitted directly in {processing_time:.2f} seconds")

			return {
				"success": True,
				"checkout_request_id": checkout_request_id,
				"invoice_name": doc.name,
				"invoice_id": doc.name,
				"invoice": _get_invoice_response_summary(doc),
				"payment_entry": None,
				"customer_credit": credit_result,
				"mpesa_reconciliation": mpesa_reconciliation,
				"processing_time": round(processing_time, 2),
			}

	except Exception as e:
		# The only failure path for a checkout. Do not inline a bare
		# `return {"success": False, ...}` here — see the "naming-counter burn guard"
		# comment above _abort_checkout for what that costs.
		return _abort_checkout(state, e)


@frappe.whitelist()
def get_checkout_request_status(checkout_request_id):
	"""Recover a checkout after a refresh or a lost HTTP response."""
	request_id = _normalize_checkout_request_id(checkout_request_id)
	if not request_id:
		return {"success": True, "checkout_status": "not_found"}

	request = _get_checkout_request(request_id)
	if not request:
		return {
			"success": True,
			"checkout_status": "not_found",
			"checkout_request_id": request_id,
		}

	response = _checkout_request_response(request)
	# The lookup itself succeeded even when the checkout it describes did not.
	response["success"] = True
	return response


def _reserve_checkout_credit(doc, credit_rows):
	"""Mark the vouchers on the in-memory invoice before submit; they settle after it.

	The receipts are allocated before submit, so they must leave the credit's share owing
	(_klik_credit_reserved), or the credit would find nothing to pay. The full-payment check
	counts the credit (_klik_customer_credit) only when it, the tender and the picked receipts
	cover the sale - at its rounded total, which the invoice's outstanding is built from.
	"""
	if not credit_rows:
		return
	credit_sum = flt(sum(r["amount"] for r in credit_rows), 2)
	doc._klik_credit_reserved = credit_sum
	receipts_total = sum(flt(c.amount) for c in doc.get("custom_mpesa_reconciled_payments") or [])
	if flt(flt(doc.paid_amount) + credit_sum + receipts_total, 2) >= flt(doc.rounded_total or doc.grand_total, 2):
		doc._klik_customer_credit = credit_sum


def _apply_checkout_credit(doc, credit_rows):
	"""Settle validated credit-note allocations right after submit; never undo the sale.

	A stale allocation (the note was spent between validation and here) must not lose
	the submitted sale: log it and tell the cashier to apply the credit from desk. A
	partial application (a note drained concurrently) is reported as partial.
	"""
	result = {"applied": 0.0, "warning": None}
	if not credit_rows:
		return result
	requested = flt(sum(r["amount"] for r in credit_rows), 2)
	try:
		applied = apply_customer_credit(doc.name, credit_rows)
		result["applied"] = applied["applied"]
		if flt(applied["applied"], 2) < requested:
			result["warning"] = (
				"Only part of the customer credit could be applied - settle the rest "
				"from desk Payment Reconciliation."
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Customer credit not applied")
		result["warning"] = (
			"Sale submitted, but the customer credit could not be applied - "
			"apply it from desk Payment Reconciliation."
		)
	return result


@frappe.whitelist()
def process_queued_sales_invoice(invoice_name, requested_by=None, customer_credit=None, requeues=0):
	"""Background worker that submits a queued draft sales invoice."""
	try:
		return _retry_lock_race(_submit_queued_invoice, invoice_name, requested_by, customer_credit)
	except Exception as e:
		frappe.db.rollback()
		if isinstance(e, LOCK_RACE_ERRORS) and int(requeues) < LOCK_RACE_REQUEUES:
			# Rolled back, so no locks are held while it waits. Frappe runs no RQ scheduler, so
			# there is no delayed enqueue: the gap is this pause plus whatever is queued ahead.
			# ponytail: blocks the worker for the pause; a delayed job if this starves the queue.
			time.sleep(random.uniform(5, 15))
			try:
				frappe.enqueue(
					"klik_pos.api.sales_invoice.process_queued_sales_invoice",
					queue="long",
					invoice_name=invoice_name,
					requested_by=requested_by,
					customer_credit=customer_credit,
					requeues=int(requeues) + 1,
				)
				return {"success": False, "message": str(e), "requeued": True}
			except Exception:
				# Not back on the queue: fail it below, so the cashier is told.
				frappe.log_error(frappe.get_traceback(), f"Queued Invoice requeue error for {invoice_name}")
		try:
			doc = frappe.get_doc("Sales Invoice", invoice_name)
			attempts = int(getattr(doc, "queue_attempts", 0) or 0) + 1
			_update_queue_fields(doc, QUEUE_STATUSES["failed"], error_message=str(e), attempts=attempts)
			doc.save(ignore_permissions=True)
			_notify_queue_failure(doc, requested_by, str(e))
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Queue failure update error for {invoice_name}")
		frappe.log_error(frappe.get_traceback(), f"Queued Invoice Submit Error for {invoice_name}")
		return {"success": False, "message": str(e)}


def _submit_queued_invoice(invoice_name, requested_by, customer_credit):
	doc = frappe.get_doc("Sales Invoice", invoice_name)
	if doc.docstatus != 0:
		_apply_klik_invoice_flags(doc, is_submitted=True)
		_update_queue_fields(doc, QUEUE_STATUSES["submitted"], None)
		doc.save(ignore_permissions=True)
		return {"success": True, "message": "Invoice already submitted"}

	attempts = int(getattr(doc, "queue_attempts", 0) or 0) + 1
	# No save for Processing: nothing commits before the end, so no one could read it, and
	# the save re-ran validate and rewrote the item-wise tax rows - where the lock races were.
	_update_queue_fields(doc, QUEUE_STATUSES["processing"], attempts=attempts)

	# The checkout's vouchers settle after submit; the receipts below must leave them their share.
	credit_sum = flt(sum(flt(r.get("amount")) for r in customer_credit or []), 2)
	doc._klik_credit_reserved = credit_sum

	# Payment-Entry-first M-Pesa: each recorded receipt becomes a Payment Entry and the
	# draft takes what it owes as advances, before submit.
	mpesa_allocation = None
	if doc.get("custom_mpesa_reconciled_payments"):
		from klik_pos.api.mpesa import _allocate_receipts_before_submit

		mpesa_allocation = _allocate_receipts_before_submit(doc)

	_apply_klik_invoice_flags(doc, is_submitted=True)
	_enforce_submit_permission(doc, user=requested_by or frappe.session.user)
	# The cashier's shift was valid when this sale was queued; a worker running
	# after midnight must not refuse it for a shift that is now stale.
	frappe.flags.klik_processing_queued_invoice = True
	# The checkout's in-memory voucher marker did not survive the queue: the vouchers
	# it validated arrive here, and they pay the sale as they did at checkout - with the
	# receipts' advances, allocated just above.
	if credit_sum and flt(
		flt(doc.paid_amount) + flt(doc.total_advance) + credit_sum, 2
	) >= flt(doc.rounded_total or doc.grand_total, 2):
		doc._klik_customer_credit = credit_sum
	try:
		doc.submit()
	finally:
		frappe.flags.klik_processing_queued_invoice = False
	doc.reload()
	try:
		_cancel_sales_invoice_reservations(doc.name)
	except Exception as e:
		_raise_lock_race(e)
		frappe.log_error(
			frappe.get_traceback(),
			f"Failed to cancel reservations after submit for {doc.name}",
		)
	if doc.get("custom_mpesa_reconciled_payments"):
		from klik_pos.api.mpesa import _finalize_mpesa_reconciliation

		# Deliberately unguarded: a finalize that fails leaves the invoice reading Paid
		# (the advances already zeroed its outstanding) with its entries unreconciled and
		# its receipts unconsumed. Let it reach the handler below, which rolls the whole
		# submission back and tells the cashier.
		_finalize_mpesa_reconciliation(doc, mpesa_allocation)
	_update_queue_fields(doc, QUEUE_STATUSES["submitted"], attempts=attempts)
	if hasattr(doc, "queue_error"):
		doc.queue_error = ""
	doc.save(ignore_permissions=True)

	_finalize_submitted_invoice(
		doc,
		flt(doc.paid_amount or 0),
		_get_payment_methods_from_invoice(doc),
		getattr(doc, "business_type", None),
		doc.customer,
	)

	credit_result = _apply_checkout_credit(doc, customer_credit or [])
	if credit_result["warning"]:
		frappe.logger().warning(f"{invoice_name}: {credit_result['warning']}")

	return {"success": True, "message": f"Invoice {invoice_name} submitted successfully"}


@frappe.whitelist()
def get_unresolved_queue_failures():
	"""Sales that were queued, failed, and still have not posted.

	The realtime alert is fire-and-forget: a cashier who had reloaded, or who was serving
	someone else when it fired, never sees it. Rather than tracking alert delivery and
	acknowledgement, derive the state - a failed queued invoice is already durable, so ask
	the question at load time and let the answer clear itself when the invoice submits.

	Scope follows the alert audience: a manager sees every unresolved failure, everyone else
	sees their own. Managers are the ones who can act on a till they are not standing at.
	"""
	try:
		user = frappe.session.user
		is_manager = bool({"Sales Manager", "System Manager"} & set(frappe.get_roles(user)))

		filters = {"docstatus": 0, "queue_status": QUEUE_STATUSES["failed"]}
		if not is_manager:
			filters["owner"] = user

		invoices = frappe.get_all(
			"Sales Invoice",
			filters=filters,
			fields=[
				"name",
				"customer",
				"customer_name",
				"grand_total",
				"currency",
				"queue_error",
				"queue_attempts",
				"modified",
			],
			order_by="modified desc",
			limit=20,
			ignore_permissions=True,
		)

		# The list is capped, the count is not: telling a till with forty unposted sales that
		# it has twenty is the same silent truncation this banner exists to prevent.
		total = frappe.db.count("Sales Invoice", filters)

		return {
			"success": True,
			"count": total,
			"shown": len(invoices),
			"invoices": [
				{
					"invoice_name": row.name,
					"customer": row.customer_name or row.customer,
					"grand_total": flt(row.grand_total or 0),
					"currency": row.currency,
					# Stripped on read as well as on write: errors stored before the write-side
					# strip landed still carry markup, and this text is rendered as text.
					"error": _truncate_queue_error(row.queue_error),
					"attempts": int(row.queue_attempts or 0),
					"failed_at": str(row.modified) if row.modified else None,
				}
				for row in invoices
			],
		}
	except Exception:
		# A status check must never be the thing that breaks the POS.
		frappe.log_error(frappe.get_traceback(), "Get Unresolved Queue Failures Error")
		return {"success": False, "count": 0, "shown": 0, "invoices": []}


@frappe.whitelist()
def retry_failed_sales_invoice(invoice_name):
	"""Retry a failed queued invoice by enqueueing it again."""
	try:
		doc = frappe.get_doc("Sales Invoice", invoice_name)
		if doc.docstatus != 0:
			frappe.throw("Only draft invoices can be retried from the queue.")

		if (getattr(doc, "queue_status", "") or "").lower() not in ("failed", "processing", "queued"):
			frappe.throw("This invoice is not in a retryable queue state.")

		if _needs_shift_check(doc):
			# A retry is a new attempt to sell, and a sale can only go through on a till
			# with a shift open today. Run this before anything below reserves stock or
			# marks the invoice Queued again, so a refusal leaves it exactly as it was.
			doc.validate_pos_opening_entry()

		# Queued first: the Stock Reservation Entry override lets only a Queued or
		# Processing klik draft reserve (klik_pos.overrides.stock_reservation_entry).
		_update_queue_fields(doc, QUEUE_STATUSES["queued"], error_message="")
		doc.save(ignore_permissions=True)

		try:
			_validate_reserved_stock_for_items(doc, exclude_invoice=doc.name)
			_reserve_stock_for_queued_invoice(doc)
		except Exception as reserve_error:
			_raise_lock_race(reserve_error)
			# Back to Failed with the reason, not Queued with no job behind it.
			_update_queue_fields(doc, QUEUE_STATUSES["failed"], error_message=str(reserve_error))
			doc.save(ignore_permissions=True)
			return {"success": False, "message": str(reserve_error)}

		frappe.enqueue(
			"klik_pos.api.sales_invoice.process_queued_sales_invoice",
			queue="long",
			enqueue_after_commit=True,
			invoice_name=doc.name,
			requested_by=frappe.session.user,
		)

		return {"success": True, "queue_status": doc.queue_status}

	except Exception as e:
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def create_draft_invoice(data):
	return _retry_lock_race(_create_draft_invoice, data)


def _create_draft_invoice(data):
	try:
		if isinstance(data, str):
			data = json.loads(data)

		target_draft_invoice_id = data.get("draft_invoice_id") if isinstance(data, dict) else None
		(
			customer,
			items,
			amount_paid,
			sales_and_tax_charges,
			mode_of_payment,
			business_type,
			roundoff_amount,
			delivery_charge,
			delivery_personnel,
			is_credit_sale,
			allow_partial_payment,
			due_date,
			salesperson,
			tax_id,
			enable_background_submission,
			loyalty_redemption,
		) = parse_invoice_data(data)

		walkin_name = data.get("walkin_name")
		walkin_phone = data.get("walkin_phone")
		extra_fields = _parse_extra_fields(data)

		if target_draft_invoice_id:
			doc = frappe.get_doc("Sales Invoice", target_draft_invoice_id)
			if doc.docstatus != 0 or doc.status != "Draft":
				frappe.throw(
					_("Cannot update invoice {0}. Only Draft invoices can be held again.").format(
						target_draft_invoice_id
					)
				)

			_apply_quote_link(doc, data)  # saved by the update below
			_update_existing_draft_invoice(
				doc,
				customer,
				items,
				amount_paid,
				sales_and_tax_charges,
				mode_of_payment,
				business_type,
				roundoff_amount,
				delivery_charge,
				delivery_personnel=delivery_personnel,
				is_credit_sale=is_credit_sale,
				allow_partial_payment=allow_partial_payment,
				due_date=due_date,
				salesperson=salesperson,
				tax_id=tax_id,
				enable_background_submission=enable_background_submission,
				loyalty_redemption=loyalty_redemption,
				walkin_name=walkin_name,
				walkin_phone=walkin_phone,
				extra_fields=extra_fields,
				order_discount_amount=flt(data.get("orderDiscountAmount") or 0),
				shipping_rule=data.get("shipping_rule") or None,
				selling_price_list=data.get("selling_price_list") or None,
			)
		else:
			doc = build_sales_invoice_doc(
				customer,
				items,
				amount_paid,
				sales_and_tax_charges,
				mode_of_payment,
				business_type,
				roundoff_amount,
				delivery_charge,
				include_payments=True,
				delivery_personnel=delivery_personnel,
				is_credit_sale=is_credit_sale,
				allow_partial_payment=allow_partial_payment,
				due_date=due_date,
				salesperson=salesperson,
				tax_id=tax_id,
				enable_background_submission=enable_background_submission,
				loyalty_redemption=loyalty_redemption,
				walkin_name=walkin_name,
				walkin_phone=walkin_phone,
				extra_fields=extra_fields,
				order_discount_amount=flt(data.get("orderDiscountAmount") or 0),
				shipping_rule=data.get("shipping_rule") or None,
				selling_price_list=data.get("selling_price_list") or None,
			)

			_apply_quote_link(doc, data)
			validate_required_salesperson(doc)
			# M-Pesa's draft, made before the money moves: a payment in progress, not a hold.
			# Held orders are Sales Orders.
			_apply_klik_invoice_flags(doc, is_held=False, is_submitted=False)
			# Money is being collected, so it is a cash sale - even with Credit Sale left ticked,
			# which the submit refuses once the payment lands.
			_set_site_sale_type(doc, is_credit_sale=False)
			_apply_remarks(doc, data)
			doc.insert(ignore_permissions=True)

		if tax_id:
			doc.db_set("tax_id", tax_id)
		if target_draft_invoice_id and isinstance(data, dict) and "remarks" in data:
			# The existing draft was saved above; its note goes on as it is.
			doc.db_set("remarks", _parse_remarks(data))

		return {"success": True, "invoice_name": doc.name, "invoice": doc}

	except Exception as e:
		_raise_lock_race(e)
		frappe.log_error(frappe.get_traceback(), "Draft Invoice Error")
		return {"success": False, "message": str(e)}
	

def parse_invoice_data(data):
	if isinstance(data, str):
		data = json.loads(data)

	def _normalize_bundle_entries(value):
		if not value:
			return []
		if isinstance(value, list):
			return value
		if isinstance(value, str):
			try:
				parsed = json.loads(value)
				return parsed if isinstance(parsed, list) else []
			except Exception:
				return []
		return []

	def _as_bool(value):
		if isinstance(value, str):
			return value.lower() in ("true", "1", "yes", "on")
		return bool(value)

	customer = data.get("customer", {}).get("id")
	items = []
	item_discounts = data.get("itemDiscounts", {})
	has_positive_priced_item = False
	raw_credit_sale_flag = data.get("isCreditSale")
	if raw_credit_sale_flag is None:
		raw_credit_sale_flag = data.get("is_credit_sale")
	is_credit_sale = _as_bool(raw_credit_sale_flag)
	allow_partial_payment = _as_bool(
		data.get("allowPartialPayment") or data.get("allow_partial_payment")
	)
	enable_background_submission = _as_bool(
		data.get("enable_background_invoice_submission") or 0
	)
	loyalty_redemption = normalize_loyalty_redemption(data)
	due_date = data.get("dueDate") or data.get("due_date")
	payment_terms_template = data.get("paymentTermsTemplate") or data.get("payment_terms_template")
	mode_of_payment = None
	default_payment_mode = None
	checkout_status = str(data.get("status") or "").strip().lower()
	has_payment_submission_context = any(
		key in data
		for key in (
			"paymentMethods",
			"amountPaid",
			"isCreditSale",
			"is_credit_sale",
			"dueDate",
			"due_date",
		)
	)

	if raw_credit_sale_flag is None and has_payment_submission_context:
		default_sales_type = (
			getattr(get_current_pos_profile(), "default_sales_type", None) or "Cash"
		).strip().lower()
		is_credit_sale = default_sales_type == "credit"

	if is_credit_sale:
		pos_profile = get_current_pos_profile()
		if not _credit_sales_allowed(pos_profile):
			if raw_credit_sale_flag is None:
				# The till's default sale type, not the cashier's choice: sell for cash.
				is_credit_sale = False
			else:
				frappe.throw(
					_(
						'Credit sales are turned off for this POS Profile. Enable "Allow Credit Sales" on {0} to use them.'
					).format(getattr(pos_profile, "name", None) or _("this POS Profile"))
				)

	allow_zero_rate_sales = cint(
		getattr(get_current_pos_profile(), "allow_zero_rate_sales", 0) or 0
	)

	for item in data.get("items", []):
		# Draft edit flows can send a unique cart-line id and the actual item code separately.
		item_code = item.get("item_code") or item.get("id")
		line_id = item.get("line_id") or item.get("id")

		discount_data = item_discounts.get(line_id) or item_discounts.get(item_code) or {}
		if isinstance(discount_data, str):
			try:
				discount_data = json.loads(discount_data)
			except Exception:
				discount_data = {}
		if not isinstance(discount_data, dict):
			discount_data = {}

		bundle_entries = _normalize_bundle_entries(
			item.get("bundle_entries")
			or item.get("serial_batch_bundle")
			or discount_data.get("bundle_entries")
			or discount_data.get("serial_batch_bundle")
		)

		item_tax_rate = item.get("item_tax_rate") or {}
		if isinstance(item_tax_rate, str):
			try:
				item_tax_rate = json.loads(item_tax_rate)
			except Exception:
				item_tax_rate = {}

		discount_percentage = flt(item.get("discountPercentage") or discount_data.get("discountPercentage") or 0)
		discount_amount = flt(item.get("discountAmount") or discount_data.get("discountAmount") or 0)

		items.append({
			"id": item_code,
			# The cart's line id: two lines of one item keep their own saved state by it.
			"line_id": line_id,
			"quantity": item.get("quantity"),
			"price": item.get("price"),
			"price_list_rate": flt(item.get("price_list_rate") or item.get("original_price") or 0),
			"bundle_entries": bundle_entries,
			"uom": item.get("uom"),
			"item_tax_template": item.get("item_tax_template") or "",
			"item_tax_rate": item_tax_rate,
			"discountPercentage": discount_percentage,
			"discountAmount": discount_amount,
			# The cashier's own wording for this line, from the cart's pen dialog.
			"description": (item.get("description") or "").strip(),
			# The list this line was priced from at the till (per-line switch); "" for the cart's own.
			"price_list": item.get("selectedPriceList") or discount_data.get("selectedPriceList") or "",
			# Asked for but not in stock: Loss of Sale, recorded on the invoice line.
			"los_qty": max(flt(item.get("los_qty") or 0), 0),
		})

		price = flt(item.get("price") or 0)
		quantity = flt(item.get("quantity") or 0)
		if price > 0 and quantity > 0:
			has_positive_priced_item = True

		if price < 0:
			frappe.throw(
				_("Rate cannot be negative for item {0}").format(
					item_code or _("Unknown Item")
				)
			)

		if (
			price == 0
			and discount_percentage <= 0
			and discount_amount <= 0
			and not allow_zero_rate_sales
		):
			frappe.throw(
				_("Rate must be greater than 0 for item {0} when no discount is set").format(
					item_code or _("Unknown Item")
				)
			)

	amount_paid = 0.0
	pos_profile = get_current_pos_profile()
	sales_and_tax_charges = pos_profile.taxes_and_charges
	business_type = data.get("businessType")

	# Customer credit tendered at checkout (open credit notes); validated against the
	# built document later - here it only counts as money for the tender checks, at the
	# same 2dp the validator uses, so a sub-cent row cannot sneak past this gate.
	credit_total = sum(
		flt(row.get("amount"), 2)
		for row in (data.get("customerCredit") or [])
		if isinstance(row, dict) and flt(row.get("amount"), 2) > 0
	)

	# ERPNext owns invoice rounding through rounding_adjustment/rounded_total.
	# Ignore legacy Klik roundOffAmount payloads to avoid duplicate write-off entries.
	roundoff_amount = 0.0
	delivery_charge = flt(data.get("deliveryCharge") or data.get("delivery_charge") or 0.0)
	if delivery_charge < 0:
		frappe.throw(_("Delivery charge cannot be negative."))

	if delivery_charge > 0:
		allow_delivery_charge = cint(getattr(pos_profile, "custom_enable_delivery_charge", 0) or 0)
		if allow_delivery_charge != 1:
			frappe.throw(_("Delivery charge is not enabled for this POS Profile."))

		delivery_item_code = (getattr(pos_profile, "custom_delivery_charge_item", None) or "").strip()
		if not delivery_item_code:
			frappe.throw(
				_("Set Delivery Charge Item on POS Profile to use delivery charges.")
			)

		delivery_item = frappe.db.get_value(
			"Item",
			delivery_item_code,
			["name", "disabled", "is_sales_item", "is_stock_item"],
			as_dict=1,
		)
		if not delivery_item or cint(delivery_item.get("disabled") or 0) == 1:
			frappe.throw(
				_("Delivery Charge Item {0} is missing or disabled.").format(
					frappe.bold(delivery_item_code)
				)
			)

		if cint(delivery_item.get("is_sales_item") or 0) != 1:
			frappe.throw(
				_("Delivery Charge Item {0} must be allowed in sales.").format(
					frappe.bold(delivery_item_code)
				)
			)

		if cint(delivery_item.get("is_stock_item") or 0) == 1:
			frappe.throw(
				_("Delivery Charge Item {0} must be a non-stock service item.").format(
					frappe.bold(delivery_item_code)
				)
			)

	if data.get("amountPaid"):
		amount_paid = data.get("amountPaid")
	if flt(amount_paid or 0) < 0:
		frappe.throw(_("Payment amounts cannot be negative."))

	if data.get("paymentMethods"):
		mode_of_payment = data.get("paymentMethods")
		if isinstance(mode_of_payment, list):
			for payment in mode_of_payment:
				if not isinstance(payment, dict):
					continue
				if flt(payment.get("amount") or 0) < 0:
					frappe.throw(_("Payment amounts cannot be negative."))

	if is_credit_sale and has_payment_submission_context:
		if payment_terms_template:
			# The till picked terms, not a date: the due date is worked out here, not trusted.
			from klik_pos.api.payment_terms import credit_due_date

			due_date = credit_due_date(payment_terms_template)
		if not due_date:
			frappe.throw(_("Please select a due date for this credit sale"))

		if customer and _is_walkin_customer(customer):
			frappe.throw(_("Credit sales require a non walk-in customer."))

		if _has_positive_payment_amount(mode_of_payment):
			frappe.throw(
				_("Credit sale cannot include payment amounts. Disable Credit Sale to collect payment.")
			)

		# Trust payment rows for credit-sale validation and always persist as unpaid.
		amount_paid = 0.0
		default_payment_mode = _get_default_payment_mode()
		mode_of_payment = _normalize_credit_sale_payment_methods(mode_of_payment, default_payment_mode)
	elif (
		has_payment_submission_context
		and checkout_status != "held"
		and has_positive_priced_item
		and not loyalty_redemption
		# Picked M-Pesa receipts pay as advances at submit, where validate_full_payment
		# checks they cover the sale.
		and not _picked_receipts(data)
	):
		if (
			flt(amount_paid or 0) <= 0 or not _has_positive_payment_amount(mode_of_payment)
		) and flt(credit_total) <= 0:
			frappe.throw(
				_("Cash sale requires at least one payment method with a positive amount.")
			)

	if data.get("SalesTaxCharges"):
		sales_and_tax_charges = data.get("SalesTaxCharges")

	delivery_personnel = data.get("deliveryPersonnel")
	salesperson = data.get("salesperson")
	tax_id = data.get("tax_id")

	if not customer or not items:
		frappe.throw(_("Customer and items are required"))

	return (
		customer,
		items,
		amount_paid,
		sales_and_tax_charges,
		mode_of_payment,
		business_type,
		roundoff_amount,
		delivery_charge,
		delivery_personnel,
		is_credit_sale,
		allow_partial_payment,
		due_date,
		salesperson,
		tax_id,
		enable_background_submission,
		loyalty_redemption,
	)


def build_sales_invoice_doc(
	customer,
	items,
	amount_paid,
	sales_and_tax_charges,
	mode_of_payment,
	business_type,
	roundoff_amount=0.0,
	delivery_charge=0.0,
	include_payments=False,
	delivery_personnel=None,
	is_credit_sale=False,
	allow_partial_payment=False,
	due_date=None,
	salesperson=None,
	tax_id=None,
	create_batch_and_serial_bundle=True,
	enable_background_submission=False,
	loyalty_redemption=None,
	walkin_name=None,
	walkin_phone=None,
	extra_fields=None,
	order_discount_amount=0.0,
	shipping_rule=None,
	selling_price_list=None,
):
	"""Main function to build a sales invoice document.

	selling_price_list: the list the cart was on, recorded when the cashier may use it.
	"""
	if shipping_rule and flt(delivery_charge) > 0:
		frappe.throw(
			_("Choose a Shipping Rule or a Delivery Charge, not both - the customer would pay for delivery twice.")
		)
	doc = frappe.new_doc("Sales Invoice")
	_apply_klik_invoice_flags(doc, is_held=False, is_submitted=False)
	# The cart already priced every line through klik_pos.api.item.pricing.get_cart_pricing,
	# which applies Pricing Rules against the same customer and qty. ERPNext applying them
	# again here lets calculate_item_rate take the `has_pricing_rules` branch and overwrite
	# the rate the cashier quoted. Setting the flag now is not enough on its own - see
	# _reassert_pos_line_prices, which sets it again once set_pos_fields has had its say.
	doc.ignore_pricing_rule = 1
	doc.customer = customer
	doc.due_date = due_date or frappe.utils.nowdate()
	_stamp_delivery_date(doc)
	doc.enable_background_invoice_submission = 1 if enable_background_submission else 0

	# Set delivery personnel if provided
	if delivery_personnel:
		doc.custom_delivery_personnel = delivery_personnel

	# Set tax ID if provided
	if tax_id:
		doc.tax_id = tax_id

	# Set walk-in buyer name/phone (guarded; fields are optional/button-installed)
	_apply_walkin_party_fields(doc, walkin_name=walkin_name, walkin_phone=walkin_phone, walkin_tax_id=tax_id)
	_apply_extra_fields(doc, extra_fields)

	# Set salesperson in sales team
	if salesperson:
		doc.append("sales_team", {
			"sales_person": salesperson,
			"allocated_percentage": 100,
		})

	# Configure POS profile and company settings
	pos_profile = _get_active_pos_profile()
	_set_pos_profile_fields(
		doc, pos_profile, customer, business_type, amount_paid, allow_partial_payment, selling_price_list
	)

	# Order-level discount, gated by the POS Profile's Allow Discount Change permission
	# (same flag that gates per-item discount fields on the frontend).
	order_discount_amount = flt(order_discount_amount or 0)
	if order_discount_amount < 0:
		frappe.throw(_("Discount amount cannot be negative."))
	if order_discount_amount > 0:
		if not cint(getattr(pos_profile, "allow_discount_change", 0) or 0):
			frappe.throw(_("Discount changes are not allowed for this POS Profile."))
		doc.apply_discount_on = "Grand Total"
		doc.discount_amount = order_discount_amount

	# Ensure batch/serial requirements are satisfied BEFORE building items
	_validate_no_variant_templates(items)
	_validate_and_autofetch_batch_and_serial(items, pos_profile)
	_validate_product_bundle_components(items, pos_profile)

	# Set posting details
	_set_posting_fields(doc)

	# Set POS opening entry
	_set_pos_opening_entry(doc)

	# Handle round-off
	_set_roundoff_fields(doc, roundoff_amount)

	# Add items to invoice
	_populate_invoice_items(doc, items, pos_profile)
	pos_line_prices = [
		(row.item_code, flt(row.rate), flt(row.price_list_rate)) for row in doc.items
	]
	delivery_item_code = _upsert_delivery_charge_service_item(doc, pos_profile, delivery_charge)

	# Taxes and charges template, per-item tax rows, the till's tax-inclusive setting, then
	# set_taxes/set_missing_values/reassert-the-till's-rates/total - shared with the held-order
	# builders in sales_order.py, so a held order is taxed exactly like its checkout invoice.
	_apply_pos_tax_treatment(
		doc, pos_profile, sales_and_tax_charges, pos_line_prices, skip_item_code=delivery_item_code
	)
	_set_total_net_weight(doc)
	_apply_pos_shipping_rule(doc, shipping_rule)
	apply_loyalty_redemption(doc, loyalty_redemption)
	if loyalty_redemption:
		doc.calculate_taxes_and_totals()

	if create_batch_and_serial_bundle:
		_create_batch_and_serial_bundle(items, doc)

	# Add payment information
	if include_payments:
		doc.is_pos = 1
		_add_payment_entries(doc, mode_of_payment)
		# ERPNext refuses a POS invoice with no payment row. A sale paid with no tender (a
		# voucher) that was not yet POS when ERPNext filled in the till's modes has none, so
		# give it the default mode at 0 - ERPNext drops zero rows on submit.
		default_mode = None if doc.get("payments") else _get_default_payment_mode()
		if default_mode:
			doc.append("payments", {"mode_of_payment": default_mode, "amount": 0})
		doc.calculate_taxes_and_totals()

	if is_credit_sale:
		# A till may sell on credit only through its one "Allow Credit Sales" checkbox, and an
		# allowed credit sale is booked as a POS sale (parse_invoice_data has refused the rest).
		doc.is_pos = _is_pos_for_credit_sale(pos_profile)
		if due_date:
			doc.due_date = due_date

	_set_site_sale_type(doc, is_credit_sale)
	_keep_line_discount_percentages(doc, items)

	_assert_pos_rates_survived(doc, pos_line_prices, skip_item_code=delivery_item_code)

	return doc


def _keep_line_discount_percentages(doc, items):
	"""Save the % a cashier gave a line, as ERPNext's form does, where the rate is exactly the
	list rate less it - ERPNext's own sum, so its next calculation keeps the % rather than
	resetting it. The till sends the discounted rate, which ERPNext records as an amount only."""
	for row, item in zip(doc.items, items, strict=False):
		pct = flt(item.get("discountPercentage"))
		if pct <= 0 or flt(row.discount_percentage) or flt(row.margin_rate_or_amount):
			continue
		amount = flt(flt(row.price_list_rate) * pct / 100, row.precision("discount_amount"))
		if flt(flt(row.price_list_rate) - amount, row.precision("rate")) == flt(row.rate, row.precision("rate")):
			row.discount_percentage = pct
			row.discount_amount = amount


SITE_SALE_TYPE_FIELD = "custom_sale_type"


def _site_sale_types():
	"""The options of the site's own Sales Invoice sale-type Select, if it has one."""
	field = frappe.get_meta("Sales Invoice").get_field(SITE_SALE_TYPE_FIELD)
	if not field or field.fieldtype != "Select":
		return set()
	return {option.strip() for option in (field.options or "").split("\n") if option.strip()}


def _set_site_sale_type(doc, is_credit_sale):
	"""Fill a site's own Cash/Credit field, where it has one; klik adds no such field.

	Sites name invoices from it (Allparts: Cash -> CS-, Credit -> INV-), and the name is
	fixed at insert, before any payment reaches the invoice - a credit sale then looks
	like an M-Pesa draft, nothing paid on either. Only the cashier's Credit Sale choice
	tells them apart. Set on the new document only: a re-saved draft keeps the type its
	name was given (_update_existing_draft_invoice and submit_draft_invoice do not copy it,
	even when a till lists it as an extra field). create_draft_invoice's M-Pesa draft is
	always Cash.
	"""
	if not {"Cash", "Credit"} <= _site_sale_types():
		return
	doc.set(SITE_SALE_TYPE_FIELD, "Credit" if is_credit_sale else "Cash")


def _apply_quote_link(doc, data):
	"""Stamp the quote this sale was loaded from; the draft keeps it through every rebuild."""
	if data.get("quotation_id") and doc.meta.has_field("custom_klik_quotation"):
		doc.custom_klik_quotation = data["quotation_id"]


def _update_existing_draft_invoice(
	invoice_doc,
	customer,
	items,
	amount_paid,
	sales_and_tax_charges,
	mode_of_payment,
	business_type,
	roundoff_amount,
	delivery_charge=0.0,
	delivery_personnel=None,
	is_credit_sale=False,
	allow_partial_payment=False,
	due_date=None,
	salesperson=None,
	tax_id=None,
	enable_background_submission=False,
	loyalty_redemption=None,
	walkin_name=None,
	walkin_phone=None,
	extra_fields=None,
	order_discount_amount=0.0,
	shipping_rule=None,
	selling_price_list=None,
):
	rebuilt_doc = build_sales_invoice_doc(
		customer,
		items,
		amount_paid,
		sales_and_tax_charges,
		mode_of_payment,
		business_type,
		roundoff_amount,
		delivery_charge,
		include_payments=True,
		delivery_personnel=delivery_personnel,
		is_credit_sale=is_credit_sale,
		allow_partial_payment=allow_partial_payment,
		due_date=due_date,
		salesperson=salesperson,
		tax_id=tax_id,
		create_batch_and_serial_bundle=False,
		enable_background_submission=enable_background_submission,
		loyalty_redemption=loyalty_redemption,
		walkin_name=walkin_name,
		walkin_phone=walkin_phone,
		extra_fields=extra_fields,
		order_discount_amount=order_discount_amount,
		shipping_rule=shipping_rule,
		selling_price_list=selling_price_list,
	)

	_set_customer(invoice_doc, rebuilt_doc.customer)
	invoice_doc.due_date = rebuilt_doc.due_date
	_stamp_delivery_date(invoice_doc, rebuilt_doc.get("custom_delivery_date"))
	invoice_doc.enable_background_invoice_submission = rebuilt_doc.enable_background_invoice_submission
	invoice_doc.custom_delivery_personnel = rebuilt_doc.custom_delivery_personnel
	invoice_doc.tax_id = rebuilt_doc.tax_id
	_apply_walkin_party_fields(
		invoice_doc,
		walkin_name=rebuilt_doc.get("custom_walkin_customer_name"),
		walkin_phone=rebuilt_doc.get("custom_walkin_phone"),
		walkin_tax_id=rebuilt_doc.get("custom_walkin_tax_id") or rebuilt_doc.tax_id,
	)
	from klik_pos.api.pos_profile import get_configured_extra_fieldnames
	for _fn in get_configured_extra_fieldnames():
		if _fn == SITE_SALE_TYPE_FIELD:
			continue  # fixed with the draft's name; see _set_site_sale_type
		if invoice_doc.meta.has_field(_fn) and rebuilt_doc.meta.has_field(_fn):
			invoice_doc.set(_fn, rebuilt_doc.get(_fn))
	invoice_doc.pos_profile = rebuilt_doc.pos_profile
	# Finished here, it counts in this till's shift - not in the one that started it.
	if rebuilt_doc.get("custom_pos_opening_entry"):
		invoice_doc.custom_pos_opening_entry = rebuilt_doc.custom_pos_opening_entry
	invoice_doc.company = rebuilt_doc.company
	invoice_doc.currency = rebuilt_doc.currency
	invoice_doc.selling_price_list = rebuilt_doc.selling_price_list
	invoice_doc.conversion_rate = rebuilt_doc.conversion_rate
	invoice_doc.update_stock = rebuilt_doc.update_stock
	invoice_doc.warehouse = rebuilt_doc.warehouse
	invoice_doc.cost_center = rebuilt_doc.cost_center
	invoice_doc.is_pos = rebuilt_doc.is_pos
	invoice_doc.redeem_loyalty_points = rebuilt_doc.redeem_loyalty_points
	invoice_doc.loyalty_points = rebuilt_doc.loyalty_points
	invoice_doc.loyalty_amount = rebuilt_doc.loyalty_amount
	invoice_doc.loyalty_program = rebuilt_doc.loyalty_program
	invoice_doc.loyalty_redemption_account = rebuilt_doc.loyalty_redemption_account
	invoice_doc.loyalty_redemption_cost_center = rebuilt_doc.loyalty_redemption_cost_center
	invoice_doc.taxes_and_charges = rebuilt_doc.taxes_and_charges
	invoice_doc.apply_discount_on = rebuilt_doc.apply_discount_on
	invoice_doc.discount_amount = rebuilt_doc.discount_amount
	invoice_doc.shipping_rule = rebuilt_doc.get("shipping_rule")
	invoice_doc.set("items", [])
	for item_row in rebuilt_doc.get("items", []):
		invoice_doc.append("items", item_row.as_dict())
	invoice_doc.set("taxes", [])
	for tax_row in rebuilt_doc.get("taxes", []):
		invoice_doc.append("taxes", tax_row.as_dict())
	invoice_doc.set("sales_team", [])
	for sales_person_row in rebuilt_doc.get("sales_team", []):
		invoice_doc.append("sales_team", sales_person_row.as_dict())

	if items:
		_create_batch_and_serial_bundle(items, invoice_doc)

	invoice_doc.set_taxes()
	# Emptied first: ERPNext refilling the till's modes over rows already here tells the
	# cashier "Payment methods refreshed" - about rows replaced just below anyway.
	invoice_doc.set("payments", [])
	_set_missing_values_keeping_warehouse(invoice_doc)
	invoice_doc.calculate_taxes_and_totals()

	# Payments must be applied after the first totals pass, then totals are recalculated
	# so ERPNext includes both payment rows and loyalty redemption in paid/outstanding amounts.
	invoice_doc.set("payments", [])
	_add_payment_entries(invoice_doc, mode_of_payment)
	invoice_doc.calculate_taxes_and_totals()

	validate_required_salesperson(invoice_doc)
	_apply_klik_invoice_flags(invoice_doc, is_held=True, is_submitted=False)
	invoice_doc.save(ignore_permissions=True)


def _select_invoice_row_for_bundle(doc, item_code, preferred_index, used_rows):
	if preferred_index < len(doc.items):
		candidate = doc.items[preferred_index]
		if candidate.item_code == item_code and candidate.name not in used_rows:
			return candidate

	for row in doc.items:
		if row.item_code == item_code and row.name not in used_rows:
			return row

	return None


def _normalize_bundle_qty_entries(row, entries, item_meta, item_code):
	cleaned_entries = []
	for entry in entries:
		if not isinstance(entry, dict):
			continue
		batch_no = entry.get("batch_no")
		serial_no = entry.get("serial_no")
		if not batch_no and not serial_no:
			continue
		cleaned_entries.append({"batch_no": batch_no, "serial_no": serial_no, "qty": flt(entry.get("qty") or 0)})

	if not cleaned_entries:
		return []

	target_qty = flt(abs(getattr(row, "stock_qty", 0) or 0))
	if target_qty <= 0:
		target_qty = flt(abs(getattr(row, "qty", 0) or 0))

	if target_qty <= 0:
		return cleaned_entries

	target_precision = row.precision("stock_qty") if hasattr(row, "precision") else 6

	if item_meta.has_serial_no:
		serial_entries = [entry for entry in cleaned_entries if entry.get("serial_no")]
		if serial_entries and flt(len(serial_entries), target_precision) != flt(target_qty, target_precision):
			frappe.throw(
				_(
					"Serial count for Item {0} is {1} but required quantity is {2}. Please reselect serial numbers."
				).format(item_code, len(serial_entries), target_qty)
			)
		for entry in cleaned_entries:
			entry["qty"] = 1 if entry.get("serial_no") else flt(abs(entry.get("qty") or 0))
		return cleaned_entries

	if len(cleaned_entries) == 1:
		cleaned_entries[0]["qty"] = target_qty
		return cleaned_entries

	current_total = flt(sum(abs(flt(entry.get("qty") or 0)) for entry in cleaned_entries))
	if flt(current_total, target_precision) != flt(target_qty, target_precision):
		frappe.throw(
			_(
				"Batch quantity for Item {0} is {1} but required quantity is {2}. Please reselect batch quantities."
			).format(item_code, current_total, target_qty)
		)

	for entry in cleaned_entries:
		entry["qty"] = flt(abs(entry.get("qty") or 0))

	return cleaned_entries

def _create_batch_and_serial_bundle(items, doc):
	used_rows = set()


	for idx, item_data in enumerate(items):
		item_code = item_data.get("item_code") or item_data.get("id")
		serial_batch_bundle = item_data.get("bundle_entries")

		if not item_code or not serial_batch_bundle:
			continue

		item_meta = frappe.db.get_value(
			"Item",
			item_code,
			["has_batch_no", "has_serial_no", "is_stock_item"],
			as_dict=1,
		)

		if not item_meta or int(item_meta.get("is_stock_item") or 0) == 0:
			continue

		if not (item_meta.has_batch_no or item_meta.has_serial_no):
			continue

		row = _select_invoice_row_for_bundle(doc, item_code, idx, used_rows)
		if not row:
			continue

		normalized_entries = _normalize_bundle_qty_entries(row, serial_batch_bundle, item_meta, item_code)
		if not normalized_entries:
			continue

		bundle = frappe.new_doc("Serial and Batch Bundle")
		bundle.item_code = item_code
		bundle.company = doc.company
		bundle.warehouse = row.warehouse
		bundle.has_batch_no = item_meta.has_batch_no
		bundle.has_serial_no = item_meta.has_serial_no
		bundle.type_of_transaction = "Outward"
		bundle.voucher_type = doc.doctype

		for entry in normalized_entries:
			bundle.append(
				"entries",
				{
					"batch_no": entry.get("batch_no"),
					"serial_no": entry.get("serial_no"),
					"qty": -abs(flt(entry.get("qty") or 0)),
				},
			)

		bundle.insert()
		row.serial_and_batch_bundle = bundle.name
		used_rows.add(row.name)


def _get_active_pos_profile():
	"""Get the active POS profile from current session or fallback to default."""
	selected_pos_profile_name = None

	try:
		current_opening_entry = get_current_pos_opening_entry()
		if current_opening_entry:
			opening_doc = frappe.get_doc("POS Opening Entry", current_opening_entry)
			selected_pos_profile_name = opening_doc.pos_profile
	except Exception:
		frappe.logger().error(f"Error getting POS Opening Entry: {frappe.get_traceback()}")
		pass

	try:
		if selected_pos_profile_name:
			pos_profile_doc = frappe.get_doc("POS Profile", selected_pos_profile_name)
			pos_profile_doc.warehouse = requested_warehouse(pos_profile_doc) or pos_profile_doc.warehouse
			return pos_profile_doc
		else:
			fallback_profile = get_current_pos_profile()
			return fallback_profile
	except Exception:
		frappe.logger().error(f"Error getting POS Profile: {frappe.get_traceback()}")
		frappe.logger().error(f"Attempted to get profile: {selected_pos_profile_name}")
		raise


def resolve_selling_price_list(requested, customer, pos_profile):
	"""The list the cart was on when the cashier may use it, else customer -> group -> till.

	The minimum selling price judges a till sale against this list, so a list the cashier has
	no User Permission for is never recorded just because the request named it.
	"""
	if requested and is_allowed_price_list(requested):
		return requested
	return get_price_list_with_customer_priority(customer, pos_profile)


LINE_PRICE_LIST_FIELD = "powerpack_line_price_list"  # cecypo_powerpack's, when installed


def set_line_price_list(row, doctype, price_list):
	"""Record the list a line was priced from (per-line switch), if the field exists and the cashier may use it."""
	if price_list and frappe.get_meta(doctype).has_field(LINE_PRICE_LIST_FIELD) and is_allowed_price_list(price_list):
		row[LINE_PRICE_LIST_FIELD] = price_list


def _set_pos_profile_fields(
	doc, pos_profile, customer, business_type, amount_paid=0.0, allow_partial_payment=False, requested_price_list=None
):
	"""Set POS profile, company, currency and POS-specific fields."""
	doc.pos_profile = pos_profile.name
	doc.company = pos_profile.company
	doc.currency = get_customer_billing_currency(customer)
	price_list = resolve_selling_price_list(requested_price_list, customer, pos_profile)
	if price_list:
		doc.selling_price_list = price_list
	doc.conversion_rate = 1.0
	doc.update_stock = 1
	doc.warehouse = pos_profile.warehouse
	doc.cost_center = pos_profile.cost_center

	# Determine if this is a POS invoice
	doc.is_pos = 1 if allow_partial_payment or flt(amount_paid or 0) > 0 else _determine_is_pos(customer, business_type)


def _validate_and_autofetch_batch_and_serial(items, pos_profile):
	"""
	Validate that all batch/serial requirements are satisfied for POS items.

	Behaviour:
	- If POS Profile.custom_autofetch_batchserial_ is truthy:
	  * For batch-tracked items missing batch, try to auto-assign a batch using FIFO.
	  * If no suitable batch is found, raise a clear error and STOP invoice creation.
	- If the flag is not set:
	  * For batch-tracked items missing batch, raise an error and STOP invoice creation.
	- For serial-tracked items we do NOT auto-assign; user must select serials explicitly.
	"""
	if not items:
		return

	item_codes = [item.get("id") for item in items if item.get("id")]
	if not item_codes:
		return

	item_data_map = _batch_fetch_item_data(item_codes)
	auto_fetch_enabled = int(getattr(pos_profile, "custom_autofetch_batchserial_", 0) or 0)

	for item in items:
		item_code = item.get("id")
		if not item_code:
			continue

		item_db_data = item_data_map.get(item_code, {}) or {}
		is_stock_item = int(item_db_data.get("is_stock_item") or 0)
		if not is_stock_item:
			continue

		has_batch_no = int(item_db_data.get("has_batch_no") or 0)
		has_serial_no = int(item_db_data.get("has_serial_no") or 0)
		bundle_entries = item.get("bundle_entries") or item.get("serial_batch_bundle") or []
		has_bundle_values = any(
			(entry.get("batch_no") or entry.get("serial_no"))
			for entry in bundle_entries
		)
		has_explicit_batch = bool(item.get("batchNumber") or item.get("batch_no"))
		has_explicit_serial = bool(item.get("serialNumber") or item.get("serial_no"))

		# Serial-tracked items must have either serial bundle entries or explicit serial data.
		if has_serial_no and not (has_bundle_values or has_explicit_serial):
			frappe.throw(
				_(
					"Serial No / Batch No are mandatory for Item {0}. Please select serial numbers before submitting the invoice."
				).format(item_code)
			)

		# Batch-tracked items must have either bundle entries, explicit batch, or be auto-fetched.
		if has_batch_no and not (has_bundle_values or has_explicit_batch):
			if auto_fetch_enabled:
				auto_batch = _autofetch_batch_fifo(item_code, pos_profile.warehouse, item.get("quantity"))
				if not auto_batch:
					frappe.throw(
						_(
							"Serial No / Batch No are mandatory for Item {0} and no suitable batch is available in warehouse {1}."
						).format(item_code, pos_profile.warehouse)
					)
				item["batchNumber"] = auto_batch
			else:
				frappe.throw(
					_(
						"Serial No / Batch No are mandatory for Item {0}. Please select a batch before submitting the invoice."
					).format(item_code)
				)


def _validate_no_variant_templates(items):
	if not items:
		return

	item_codes = [item.get("id") for item in items if item.get("id")]
	if not item_codes:
		return

	templates = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes], "has_variants": 1},
		pluck="name",
	)
	if templates:
		frappe.throw(
			_(
				"Item {0} is a variant template. Please select a specific variant before submitting the invoice."
			).format(", ".join(templates))
		)


def _validate_product_bundle_components(items, pos_profile):
	if not items:
		return

	bundle_item_codes = [item.get("id") for item in items if item.get("id")]
	if not bundle_item_codes:
		return

	placeholders = ", ".join(["%s"] * len(bundle_item_codes))
	bundle_rows = frappe.db.sql(
		f"""
		SELECT
			pb.new_item_code AS bundle_item_code,
			pbi.item_code,
			pbi.qty,
			i.item_name,
			i.is_stock_item,
			i.has_batch_no,
			i.has_serial_no
		FROM `tabProduct Bundle` pb
		INNER JOIN `tabProduct Bundle Item` pbi ON pbi.parent = pb.name
		INNER JOIN `tabItem` i ON i.name = pbi.item_code
		WHERE pb.disabled = 0
		AND pb.new_item_code IN ({placeholders})
		ORDER BY pb.new_item_code, pbi.idx
		""",
		tuple(bundle_item_codes),
		as_dict=True,
	)

	if not bundle_rows:
		return

	rows_by_bundle = {}
	for row in bundle_rows:
		rows_by_bundle.setdefault(row.bundle_item_code, []).append(row)

	for item in items:
		bundle_item_code = item.get("id")
		component_rows = rows_by_bundle.get(bundle_item_code)
		if not component_rows:
			continue

		parent_qty = flt(item.get("quantity") or 0)
		for component in component_rows:
			component_name = component.item_name or component.item_code
			if cint(component.has_batch_no) or cint(component.has_serial_no):
				frappe.throw(
					_(
						"Product Bundle {0} contains tracked component {1}. Klik POS does not yet support batch or serial selection for bundled components."
					).format(bundle_item_code, component_name)
				)

			if not cint(component.is_stock_item):
				continue

			required_qty = flt(component.qty or 0) * parent_qty
			available_qty = flt(
				frappe.db.get_value(
					"Bin",
					{"item_code": component.item_code, "warehouse": pos_profile.warehouse},
					"actual_qty",
				)
				or 0
			)
			if available_qty < required_qty:
				frappe.throw(
					_(
						"Insufficient stock for Product Bundle {0}. Component {1} requires {2}, but only {3} is available in warehouse {4}."
					).format(
						bundle_item_code,
						component_name,
						required_qty,
						available_qty,
						pos_profile.warehouse,
					)
				)

def _autofetch_batch_fifo(item_code, warehouse, qty):
	from erpnext.stock.doctype.batch.batch import get_batch_qty
	from frappe.utils import getdate, nowdate

	today = nowdate()
	required_qty = flt(qty or 0)

	# Walk batches in FIFO order and return the first usable batch.
	batches = frappe.get_all(
		"Batch",
		filters={
			"item": item_code,
			"disabled": 0,
		},
		fields=["name", "batch_id", "expiry_date", "creation"],
		order_by="expiry_date asc, creation asc",
	)

	for batch in batches:
		if batch.expiry_date and getdate(batch.expiry_date) < getdate(today):
			continue

		available_qty = flt(get_batch_qty(batch_no=batch.name, warehouse=warehouse) or 0)
		if available_qty >= required_qty:
			return batch.name

	item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code
	frappe.throw(
		f"No batch with sufficient stock found for item {item_name} ({item_code}) "
		f"in warehouse {warehouse}. Required: {qty}"
	)

# def _autofetch_batch_fifo(item_code, warehouse, qty):
# 	"""
# 	Simple FIFO-based batch selector.

# 	Strategy:
# 	- Prefer non-expired batches for the given item.
# 	- Order by expiry_date ASC, then creation ASC (FIFO style).
# 	- Currently does NOT enforce per-warehouse stock; core ERPNext validations
# 	  will still ensure there is sufficient stock when the invoice is submitted.
# 	"""
# 	from frappe.utils import nowdate

# 	today = nowdate()

# 	# Filter by item and non-expired batches; ignore disabled batches
# 	batches = frappe.get_all(
# 		"Batch",
# 		filters={
# 			"item": item_code,
# 			"disabled": 0,
# 			"expiry_date": [">=", today],
# 		},
# 		fields=["name", "expiry_date", "creation"],
# 		order_by="expiry_date asc, creation asc",
# 		limit_page_length=1,
# 	)

# 	if not batches:
# 		# Fallback: try ANY active batch if no expiry_date / future-dated batches exist
# 		batches = frappe.get_all(
# 			"Batch",
# 			filters={
# 				"item": item_code,
# 				"disabled": 0,
# 			},
# 			fields=["name", "creation"],
# 			order_by="creation asc",
# 			limit_page_length=1,
# 		)

# 	return batches[0].name if batches else None

def _determine_is_pos(customer, business_type):
	"""Determine if the invoice should be marked as POS based on business type."""
	if business_type == "B2C":
		return 1
	elif business_type == "B2B":
		return 0
	elif business_type == "B2B & B2C":
		return _check_customer_type_for_pos(customer)
	else:
		return 0


def _credit_sales_allowed(pos_profile):
	"""Whether this till may sell on credit - its one "Allow Credit Sales" checkbox
	(custom_allow_credit_sales_as_pos). An allowed credit sale is booked as a POS sale.

	There used to be two near-identical boxes: this one (2026-07), which only decided how an
	allowed credit sale was booked, and the older custom_allow_credit_sales (2025-08), which
	since f5a31b4 decided whether it was allowed. That one is gone; patch one_credit_checkbox
	carried each till's permission over to this one.
	"""
	return 1 if cint(getattr(pos_profile, "custom_allow_credit_sales_as_pos", 0) or 0) else 0


def _is_pos_for_credit_sale(pos_profile):
	"""Whether a credit sale is booked as a POS sale: always, where the till allows credit at
	all - the one checkbox both permits it and books it so."""
	return _credit_sales_allowed(pos_profile)


def _check_customer_type_for_pos(customer):
	"""Check if customer is an individual for B2B & B2C business type."""
	global _cached_customer_data
	if customer not in _cached_customer_data:
		_cached_customer_data[customer] = frappe.get_doc("Customer", customer)

	customer_doc = _cached_customer_data[customer]
	return 1 if customer_doc.customer_type == "Individual" else 0


def _set_posting_fields(doc):
	"""Set posting date, time and related fields."""
	doc.posting_date = frappe.utils.nowdate()
	doc.posting_time = frappe.utils.nowtime()
	doc.set_posting_time = 1


def _set_pos_opening_entry(doc):
	"""Set the current POS opening entry on the document."""
	current_opening_entry = get_current_pos_opening_entry()
	if current_opening_entry:
		doc.custom_pos_opening_entry = current_opening_entry


def _set_roundoff_fields(doc, roundoff_amount):
	"""Legacy no-op: ERPNext handles invoice rounding natively."""
	return


def _stamp_delivery_date(doc, value=None):
	"""A site's own delivery-date field: stamped only where the site has it.

	klik ships no custom_delivery_date and never reads it back - it exists for site-side
	reporting. Without the field the write silently vanished on save; skipping it keeps
	the document honest. Rebuild paths pass the rebuilt document's value through.
	"""
	if doc.meta.has_field("custom_delivery_date"):
		doc.custom_delivery_date = value or frappe.utils.nowdate()


def _set_taxes_and_charges(doc, sales_and_tax_charges, pos_profile):
	"""Set the taxes and charges template: the one chosen at checkout, else the till's, else
	the company's default - ERPNext's own fallback, applied here rather than left to it.

	ERPNext fills in the default only on a new document, and only after klik has built the
	tax rows. Left to it, a re-held order (an existing Sales Order) lost its tax altogether,
	and on a till whose rates include tax the default was added on top of the price.
	"""
	from klik_pos.api.tax import resolve_pos_tax_template

	template = sales_and_tax_charges or resolve_pos_tax_template(pos_profile, company=doc.get("company"))
	doc.taxes_and_charges = template
	return template


def _apply_pos_tax_treatment(
	doc, pos_profile, sales_and_tax_charges, pos_line_prices, skip_item_code=None, restore_price_list_rate=True
):
	"""Give a POS document (Sales Order or Sales Invoice) the same tax treatment: the till's
	taxes-and-charges template, per-item tax rows, and - when the POS Profile treats entered
	rates as tax-inclusive - inclusive tax on every row those create; then put the till's own
	rates back, since set_missing_values can still reach for a Pricing Rule between here and
	the final total.

	Shared by build_sales_invoice_doc and the held-order builders in klik_pos.api.sales_order
	(_build_sales_order_doc / _rebuild_sales_order), so a held order and the invoice checked
	out from it are taxed identically. A held order used to only set taxes_and_charges and
	never apply the till's tax-inclusive setting or per-item tax rows at all - so it priced
	its net total as though every rate were tax-exclusive, its own totals were wrong, the
	minimum-selling-price floor was checked on a different net than the invoice would use,
	and an approved order's price snapshot could never cover its own checkout invoice.

	Callers must have already appended every item row (and, for an invoice, the delivery
	charge service item, named in skip_item_code) before calling this - it builds the tax
	rows and pos_line_prices from what is already on doc.items.
	"""
	template = _set_taxes_and_charges(doc, sales_and_tax_charges, pos_profile)
	force_inclusive_tax = _is_pos_profile_tax_included_in_basic_rate(pos_profile)
	_populate_tax_details(doc, force_inclusive_tax=force_inclusive_tax)
	_populate_per_item_taxes(doc, pos_profile, force_inclusive_tax=force_inclusive_tax)
	doc.set_taxes()
	_set_missing_values_keeping_warehouse(doc)
	# On a POS invoice ERPNext's set_pos_fields copies the till's own template over this one -
	# blank on a till that names none - and leaves the rows built from it behind.
	if template and doc.get("taxes_and_charges") != template:
		doc.taxes_and_charges = template
	_reassert_pos_line_prices(
		doc, pos_line_prices, skip_item_code=skip_item_code, restore_price_list_rate=restore_price_list_rate
	)
	doc.calculate_taxes_and_totals()
	return force_inclusive_tax


def _upsert_delivery_charge_service_item(doc, pos_profile, delivery_charge):
	"""Create or update a configured delivery service item row using checkout delivery charge."""
	charge = flt(delivery_charge or 0)
	if charge <= 0:
		return

	enabled = cint(getattr(pos_profile, "custom_enable_delivery_charge", 0) or 0) == 1
	if not enabled:
		return

	delivery_item_code = (getattr(pos_profile, "custom_delivery_charge_item", None) or "").strip()
	if not delivery_item_code:
		frappe.throw(_("Set Delivery Charge Item on POS Profile to use delivery charges."))

	delivery_item = frappe.db.get_value(
		"Item",
		delivery_item_code,
		["name", "item_name", "disabled", "is_sales_item", "is_stock_item", "stock_uom"],
		as_dict=1,
	)
	if not delivery_item or cint(delivery_item.get("disabled") or 0) == 1:
		frappe.throw(
			_("Delivery Charge Item {0} is missing or disabled.").format(
				frappe.bold(delivery_item_code)
			)
		)

	if cint(delivery_item.get("is_sales_item") or 0) != 1:
		frappe.throw(
			_("Delivery Charge Item {0} must be allowed in sales.").format(
				frappe.bold(delivery_item_code)
			)
		)

	if cint(delivery_item.get("is_stock_item") or 0) == 1:
		frappe.throw(
			_("Delivery Charge Item {0} must be a non-stock service item.").format(
				frappe.bold(delivery_item_code)
			)
		)

	delivery_row = None
	for row in getattr(doc, "items", []) or []:
		if row.item_code == delivery_item_code:
			delivery_row = row
			break

	if delivery_row:
		delivery_row.qty = 1
		delivery_row.uom = delivery_row.uom or delivery_item.get("stock_uom") or "Nos"
		delivery_row.rate = charge
		delivery_row.price_list_rate = charge
		delivery_row.amount = charge
		delivery_row.base_rate = charge
		delivery_row.base_amount = charge
		return delivery_item_code

	item_data_map = _batch_fetch_item_data([delivery_item_code])
	_precache_item_accounts([delivery_item_code], pos_profile.company)
	delivery_item_payload = {
		"id": delivery_item_code,
		"quantity": 1,
		"price": charge,
		"uom": delivery_item.get("stock_uom") or "Nos",
		"item_tax_template": "",
		"item_tax_rate": {},
		"discountPercentage": 0,
		"discountAmount": 0,
	}
	doc.append("items", _prepare_item_data(doc, delivery_item_payload, item_data_map, pos_profile))

	return delivery_item_code


def _is_pos_profile_tax_included_in_basic_rate(pos_profile):
	"""Return True when Klik POS should treat entered rates as tax-inclusive."""
	if not pos_profile:
		return False
	return cint(getattr(pos_profile, "is_tax_included_in_basic_rate", 0)) == 1


def _populate_invoice_items(doc, items, pos_profile):
	"""Add all items to the invoice."""
	item_codes = [item.get("id") for item in items]

	# Batch fetch item data and pre-cache accounts
	item_data_map = _batch_fetch_item_data(item_codes)
	_precache_item_accounts(item_codes, pos_profile.company)

	# Add each item to the invoice
	for item in items:
		item_data = _prepare_item_data(doc, item, item_data_map, pos_profile)
		doc.append("items", item_data)


def _batch_fetch_item_data(item_codes):
	"""Batch fetch item data for all items."""
	if not item_codes:
		return {}

	placeholders, params = _sql_in_clause(item_codes)
	item_query = """
		SELECT name, has_batch_no, has_serial_no, is_stock_item
		FROM `tabItem`
		WHERE name IN ({})
	""".format(placeholders)

	item_results = frappe.db.sql(item_query, params, as_dict=True)
	return {item.name: item for item in item_results}


def _precache_item_accounts(item_codes, company):
	"""Pre-cache income and expense accounts for all items."""
	if not item_codes:
		return

	# Cache company data
	if company not in _cached_company_data:
		_cached_company_data[company] = frappe.get_doc("Company", company)

	company_doc = _cached_company_data[company]
	income_account = company_doc.default_income_account
	expense_account = company_doc.default_expense_account

	# Pre-populate account cache
	for item_code in item_codes:
		_cached_item_accounts[item_code] = income_account
		_cached_item_accounts[f"{item_code}_expense"] = expense_account


def _resolve_item_tax_details_for_line(doc, item, pos_profile):
	"""Resolve item tax template/rate using ERPNext item detail context."""
	item_code = item.get("id")
	if not item_code:
		return "", {}

	item_tax_template = item.get("item_tax_template") or ""
	item_tax_rate = item.get("item_tax_rate") or {}

	if isinstance(item_tax_rate, str):
		try:
			item_tax_rate = json.loads(item_tax_rate)
		except Exception:
			item_tax_rate = {}

	price_list = doc.selling_price_list or pos_profile.selling_price_list
	currency = doc.currency or frappe.get_cached_value("Company", doc.company, "default_currency")
	price_list_currency = (
		frappe.get_cached_value("Price List", price_list, "currency") if price_list else None
	) or currency

	tax_category = None
	if doc.customer and doc.customer not in _cached_customer_data:
		_cached_customer_data[doc.customer] = frappe.get_doc("Customer", doc.customer)
	if doc.customer:
		customer_doc = _cached_customer_data.get(doc.customer)
		tax_category = customer_doc.tax_category if customer_doc else None

	ctx = {
		"item_code": item_code,
		"warehouse": pos_profile.warehouse,
		"customer": doc.customer,
		"company": doc.company,
		"currency": currency,
		"conversion_rate": flt(doc.conversion_rate or 1.0),
		"price_list": price_list,
		"selling_price_list": price_list,
		"price_list_currency": price_list_currency,
		"plc_conversion_rate": 1.0,
		"qty": flt(item.get("quantity") or 1),
		"uom": item.get("uom") or "Nos",
		"tax_category": tax_category,
		"is_pos": 1,
		"doctype": "Sales Invoice",
		"name": "",
		# doc.posting_date via attribute access raises on a Sales Order (no such field there -
		# it has transaction_date instead); .get() is safe on either doctype.
		"transaction_date": doc.get("posting_date") or doc.get("transaction_date") or nowdate(),
		"item_tax_template": item_tax_template,
	}

	if price_list_currency != currency:
		ctx["plc_conversion_rate"] = flt(
			frappe.get_cached_value(
				"Currency Exchange",
				{"from_currency": price_list_currency, "to_currency": currency},
				"exchange_rate",
			)
			or 1.0
		)

	try:
		resolved = get_item_details(ctx=frappe._dict(ctx))
		item_tax_template = resolved.get("item_tax_template") or item_tax_template
		item_tax_rate = resolved.get("item_tax_rate") or item_tax_rate
		if isinstance(item_tax_rate, str):
			try:
				item_tax_rate = json.loads(item_tax_rate)
			except Exception:
				item_tax_rate = {}
	except Exception:
		pass

	return item_tax_template, item_tax_rate


def _assert_pos_rates_survived(doc, pos_line_prices, skip_item_code=None):
	"""Stop the sale if the server priced a line differently from the till.

	_reassert_pos_line_prices removes the path we found, but a Pricing Rule is not the
	only way ERPNext can rewrite a rate, and a silently repriced line means the customer
	pays something other than the number they were shown. Fail the checkout instead.
	"""
	for idx, entry in enumerate(pos_line_prices):
		if idx >= len(doc.items):
			break

		item_code, expected_rate = entry[0], entry[1]
		row = doc.items[idx]
		if skip_item_code and row.item_code == skip_item_code:
			continue

		if abs(flt(row.rate) - flt(expected_rate)) <= 0.005:
			continue

		frappe.throw(
			_(
				"Price changed for {0} during checkout. The till sent {1} and the server "
				"priced it at {2}. Check for a Pricing Rule or Item Price that disagrees "
				"with the POS price before selling this item."
			).format(
				frappe.bold(item_code),
				frappe.bold(fmt_money(flt(expected_rate), currency=doc.currency)),
				frappe.bold(fmt_money(flt(row.rate), currency=doc.currency)),
			),
			title=_("POS price overridden"),
		)


def _set_total_net_weight(doc):
	"""Fill each line's total_weight and the invoice's total_net_weight from item weights.

	ERPNext only does this in calculate_net_weight, which klik_pos never reaches while
	building a POS invoice. A "Net Weight" Shipping Rule reads total_net_weight, and the
	till shows it at checkout, so it has to be right before either looks.
	"""
	total = 0.0
	for row in doc.items:
		weight_per_unit = flt(row.get("weight_per_unit"))
		if not weight_per_unit:
			weight_per_unit = flt(frappe.get_cached_value("Item", row.item_code, "weight_per_unit"))
			row.weight_per_unit = weight_per_unit
		stock_qty = flt(row.get("stock_qty")) or flt(row.qty) * (flt(row.get("conversion_factor")) or 1)
		row.total_weight = flt(stock_qty * weight_per_unit)
		total += row.total_weight
	doc.total_net_weight = flt(total)


def _apply_pos_shipping_rule(doc, shipping_rule):
	"""Add the chosen Shipping Rule's charge row to a POS invoice.

	ERPNext's calculate_shipping_charges returns early for is_pos documents, so a rule set on
	a POS invoice never charges anything by itself. Apply it here once the lines are priced;
	the charge is an "Actual" tax row, which later totals passes keep as it is.
	"""
	if not shipping_rule:
		return

	rule = frappe.get_cached_doc("Shipping Rule", shipping_rule)
	if cint(rule.disabled) or rule.shipping_rule_type != "Selling" or rule.company != doc.company:
		frappe.throw(
			_("Shipping Rule {0} cannot be used for a sale by {1}.").format(
				frappe.bold(shipping_rule), frappe.bold(doc.company)
			)
		)

	doc.shipping_rule = shipping_rule
	rule.apply(doc)
	doc.calculate_taxes_and_totals()


def _set_missing_values_keeping_warehouse(doc):
	"""set_missing_values, leaving each line on the warehouse klik gave it.

	On a POS invoice ERPNext's set_pos_fields reloads the POS Profile and writes its warehouse
	over every line - the till's own, though the cashier picked another to sell from.
	"""
	warehouses = [row.warehouse for row in doc.items]
	doc.set_missing_values()
	for row, warehouse in zip(doc.items, warehouses):
		if warehouse:
			row.warehouse = warehouse


def _reassert_pos_line_prices(doc, pos_line_prices, skip_item_code=None, restore_price_list_rate=True):
	"""Put the till's price back on every line, and stop ERPNext reaching for it again.

	`doc.ignore_pricing_rule` cannot just be set once at the top: SalesInvoice.set_pos_fields
	copies the POS Profile's own value over it on the first (for_validate=False) pass, so
	set_missing_values re-applies Pricing Rules whatever we set beforehand, and
	apply_pricing_rule_on_items rewrites `rate` from the Item Price. Undo that here and set
	the flag again - every later pass runs with for_validate=True, which leaves the flag
	alone, so this is the last chance ERPNext gets to reprice a POS line.

	`skip_item_code` is the delivery charge item. When it is already in the cart,
	_upsert_delivery_charge_service_item writes the keyed charge onto that same row, so
	restoring the cart price by index here would silently drop the delivery charge.

	`restore_price_list_rate=False` leaves price_list_rate exactly as set_missing_values
	computed it (a fresh Item Price lookup), instead of pinning it back to whatever the
	caller captured beforehand. Held Sales Orders need this: klik's own heldOrderToCart
	recall round-trips the list price back into the client payload as "original_price",
	and pinning price_list_rate from that on every re-hold let it drift with whatever a
	Pricing Rule margin had done to it on the previous cycle (hold, recall, hold again -
	the discount grew each time). A Sales Invoice pins deliberately - see
	_prepare_item_data - because letting set_missing_values reach for the Item Price
	there records a discount the cashier never gave.
	"""
	doc.ignore_pricing_rule = 1

	for idx, (item_code, rate, price_list_rate) in enumerate(pos_line_prices):
		if idx >= len(doc.items):
			break

		row = doc.items[idx]
		if row.item_code != item_code:
			continue
		if skip_item_code and row.item_code == skip_item_code:
			continue

		row.pricing_rules = ""
		row.margin_type = None
		row.margin_rate_or_amount = 0.0
		row.rate_with_margin = 0.0
		# Left in place, a rule's discount is re-subtracted from the rate we just restored.
		# calculate_item_rate recomputes the discount from price_list_rate and rate anyway.
		row.discount_percentage = 0.0
		row.discount_amount = 0.0
		if restore_price_list_rate:
			row.price_list_rate = price_list_rate
		row.rate = rate


def _prepare_item_data(doc, item, item_data_map, pos_profile):
	"""Prepare item data dictionary for invoice line."""
	item_code = item.get("id")

	# Get accounts and validate
	income_account = get_income_accounts(item_code)
	expense_account = get_expense_accounts(item_code)
	_validate_item_accounts(item_code, income_account, expense_account)

	# Pin price_list_rate as well as rate. Left unset, set_missing_values fetches the
	# Item Price and ERPNext reconstructs the line from it - recording a discount the
	# cashier never gave, or (with a Pricing Rule in play) replacing the rate outright.
	rate = flt(item.get("price") or 0)
	price_list_rate = flt(item.get("price_list_rate") or 0)
	if price_list_rate < rate:
		price_list_rate = rate

	# Build base item data
	item_data = {
		"item_code": item_code,
		"qty": item.get("quantity"),
		"rate": rate,
		"price_list_rate": price_list_rate,
		"income_account": income_account,
		"expense_account": expense_account,
		"warehouse": pos_profile.warehouse,
		"cost_center": pos_profile.cost_center,
	}
	# Only when the cashier wrote one: set_missing_values fills the Item's own otherwise.
	if item.get("description"):
		item_data["description"] = item.get("description")
	if flt(item.get("los_qty")) > 0 and cint(pos_profile.get(LOS_PROFILE_FLAG)):
		item_data["custom_los_qty"] = flt(item.get("los_qty"))
		if not flt(item.get("quantity")):
			# set_missing_values fills a missing stock_qty with 1, which stock checks would count.
			item_data["stock_qty"] = 0

	# Resolve per-item tax fields using ERPNext item selection logic.
	item_tax_template, item_tax_rate = _resolve_item_tax_details_for_line(doc, item, pos_profile)
	if item_tax_template:
		item_data["item_tax_template"] = item_tax_template
	if item_tax_rate:
		item_data["item_tax_rate"] = frappe.as_json(item_tax_rate)

	set_line_price_list(item_data, "Sales Invoice Item", item.get("price_list"))

	# Add optional fields
	_add_uom_to_item(item_data, item)
	_add_batch_to_item(item_data, item, item_data_map.get(item_code, {}))
	_add_serial_to_item(item_data, item)

	return item_data


def _validate_item_accounts(item_code, income_account, expense_account):
	"""Validate that required accounts exist for the item."""
	if not income_account:
		frappe.throw(
			f"Income account not found for item {item_code}. "
			"Please check item defaults or company settings."
		)
	if not expense_account:
		frappe.throw(
			f"Expense account not found for item {item_code}. "
			"Please check item defaults or company settings."
		)


def _add_uom_to_item(item_data, item):
	"""Add UOM to item data if specified and not default."""
	selected_uom = item.get("uom")
	if selected_uom and selected_uom != "Nos":
		item_data["uom"] = selected_uom


def _add_batch_to_item(item_data, item, item_db_data):
	"""Add batch information if item has batch tracking."""
	is_stock_item = int(item_db_data.get("is_stock_item") or 0)
	if not is_stock_item:
		return

	has_batch_no = item_db_data.get("has_batch_no", 0)
	batch_number = item.get("batchNumber")

	if has_batch_no and batch_number:
		item_data["use_serial_batch_fields"] = 1
		item_data["batch_no"] = batch_number


def _add_serial_to_item(item_data, item):
	"""Add serial number if provided."""
	serial_number = item.get("serialNumber")
	if serial_number:
		item_data["use_serial_batch_fields"] = 1
		item_data["serial_no"] = serial_number


def _populate_tax_details(doc, force_inclusive_tax=False):
	"""Populate tax details from the taxes and charges template."""
	if not doc.taxes_and_charges:
		return

	tax_doc = get_tax_template(doc.taxes_and_charges)
	if not tax_doc:
		return

	for tax in tax_doc.taxes:
		included_in_print_rate = bool(tax.included_in_print_rate)
		if force_inclusive_tax and tax.charge_type == "On Net Total":
			included_in_print_rate = True

		doc.append(
			"taxes",
			{
				"charge_type": tax.charge_type,
				"account_head": tax.account_head,
				"description": tax.description,
				"cost_center": tax.cost_center,
				"rate": tax.rate,
				"row_id": tax.row_id,
				"tax_amount": tax.tax_amount,
				"included_in_print_rate": included_in_print_rate,
			},
		)


def _populate_per_item_taxes(doc, pos_profile, force_inclusive_tax=False):
	"""Build tax rows from per-item tax rates on invoice items.
	
	When items have item_tax_template set (e.g., from Item Tax Template),
	ERPNext stores the tax rates in item_tax_rate but doesn't automatically
	create tax rows. This function aggregates those per-item taxes and creates
	proper tax rows.
	"""
	# Collect per-account tax rates from item_tax_rate maps.
	# Do not pre-compute tax_amount here; ERPNext handles inclusive/exclusive
	# math correctly during calculate_taxes_and_totals().
	tax_aggregates = {}  # {account_head: {"rate": 0, "description": ""}}

	for item in doc.items or []:
		if not item.item_tax_rate:
			continue

		item_tax_rate = item.item_tax_rate
		if isinstance(item_tax_rate, str):
			try:
				item_tax_rate = json.loads(item_tax_rate)
			except Exception:
				continue

		for account_head, tax_rate in (item_tax_rate or {}).items():
			tax_rate_pct = flt(tax_rate or 0)
			if tax_rate_pct <= 0:
				continue

			if account_head not in tax_aggregates:
				tax_aggregates[account_head] = {
					"rate": tax_rate_pct,
					"description": account_head,
				}

	if not tax_aggregates:
		return

	# Get included_in_print_rate settings from the active taxes template.
	included_map = {}
	template_name = doc.taxes_and_charges or pos_profile.taxes_and_charges
	if template_name:
		for row in frappe.get_all(
			"Sales Taxes and Charges",
			filters={
				"parent": template_name,
				"account_head": ["in", list(tax_aggregates.keys())],
			},
			fields=["account_head", "included_in_print_rate"],
		):
			included_map[row.account_head] = bool(row.included_in_print_rate)

	# Avoid duplicating account rows if template-level taxes already populated them.
	existing_accounts = {
		row.account_head
		for row in (doc.get("taxes") or [])
		if row.account_head
	}

	# Create missing tax rows for per-item taxes.
	for account_head, tax_data in tax_aggregates.items():
		if account_head in existing_accounts:
			continue

		included_in_print_rate = included_map.get(account_head, False)
		if force_inclusive_tax:
			included_in_print_rate = True

		doc.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": account_head,
				"description": tax_data["description"],
				"rate": tax_data["rate"],
				"included_in_print_rate": included_in_print_rate,
			},
		)

def _picked_receipts(data):
	"""The checkout's M-Pesa receipt picks ({"mode_of_payment", "payments"}), or None."""
	receipts = (data or {}).get("mpesaReceipts")
	if isinstance(receipts, str):
		receipts = json.loads(receipts)
	return receipts if receipts and receipts.get("payments") else None


def _add_payment_entries(doc, mode_of_payment):
	"""Add payment entries to the invoice."""
	if not isinstance(mode_of_payment, list):
		return

	# set_pos_fields pre-fills every till mode at 0. ERPNext gives change whenever any row is
	# Cash-type, amount or not, so a zero Cash row turned a bank/M-Pesa overpayment into
	# drawer change. Keep only what was tendered; ERPNext drops zero rows on submit anyway.
	doc.set("payments", [p for p in doc.get("payments") or [] if flt(p.amount)])

	for payment in mode_of_payment:
		payment_row = {
			"mode_of_payment": payment.get("method"),
			"amount": payment.get("amount"),
		}

		if payment.get("reference_no"):
			payment_row["reference_no"] = payment.get("reference_no")
		if payment.get("phone_number"):
			payment_row["phone_number"] = payment.get("phone_number")
		if payment.get("type"):
			payment_row["type"] = payment.get("type")
		if payment.get("custom_reference_text"):
			payment_row["custom_reference_text"] = payment.get("custom_reference_text")

		doc.append("payments", payment_row)


def _get_default_payment_mode():
	"""Return the default payment mode for the active POS profile, or a safe fallback."""
	try:
		pos_profile = get_current_pos_profile()
		payment_methods = frappe.get_all(
			"POS Payment Method",
			filters={"parent": pos_profile.name},
			fields=["mode_of_payment", "default"],
			order_by="idx asc",
		)

		if not payment_methods:
			return None

		default_mode = next((row["mode_of_payment"] for row in payment_methods if row.get("default") in (1, True)), None)
		return default_mode or payment_methods[0].get("mode_of_payment")
	except Exception:
		return None


def _normalize_credit_sale_payment_methods(payment_methods, default_payment_mode):
	"""Ensure credit sales carry a zero-amount default payment row when no amount is entered."""
	if not isinstance(payment_methods, list):
		payment_methods = []

	if _has_positive_payment_amount(payment_methods):
		return payment_methods

	if payment_methods:
		for payment in payment_methods:
			if not payment.get("amount"):
				payment["amount"] = 0.0
		return payment_methods

	if default_payment_mode:
		return [{"method": default_payment_mode, "amount": 0.0}]

	return payment_methods


def _has_positive_payment_amount(payment_methods):
	"""Check whether any payment method has a positive amount."""
	if not isinstance(payment_methods, list):
		return False

	for payment in payment_methods:
		try:
			if flt(payment.get("amount") or 0) > 0:
				return True
		except Exception:
			continue
	return False


def _is_walkin_customer(customer):
	"""Return True when the selected customer is marked as walk-in."""
	if not customer:
		return False

	try:
		is_walkin = frappe.db.get_value("Customer", customer, "custom_is_walkin")
		return cint(is_walkin or 0) == 1
	except Exception:
		return False


def get_tax_template(template_name):
	"""
	Optimized tax template getter with caching.
	Custom helper function to fetch Sales Taxes and Charges Template.
	Returns the full template document or raises an error if not found.
	"""
	global _cached_item_accounts

	if not template_name:
		return None

	cache_key = f"tax_template_{template_name}"
	if cache_key not in _cached_item_accounts:
		try:
			template_doc = frappe.get_doc("Sales Taxes and Charges Template", template_name)
			_cached_item_accounts[cache_key] = template_doc
		except frappe.DoesNotExistError:
			frappe.throw(f"Tax Template '{template_name}' not found")
		except Exception as e:
			frappe.log_error(f"Error fetching tax template {template_name}: {e!s}")
			_cached_item_accounts[cache_key] = None

	return _cached_item_accounts[cache_key]


def get_customer_billing_currency(customer):
	try:
		customer_doc = frappe.get_doc("Customer", customer)
		if customer_doc.default_currency:
			return customer_doc.default_currency
	except Exception:
		pass

	# Fallback to company currency
	pos_profile = get_current_pos_profile()
	company_doc = frappe.get_doc("Company", pos_profile.company)
	return company_doc.default_currency


def get_income_accounts(item_code):
	"""Optimized income account getter with caching"""
	global _cached_item_accounts

	if item_code not in _cached_item_accounts:
		try:
			pos_profile = get_current_pos_profile()
			company = pos_profile.company

			# Cache company data
			if company not in _cached_company_data:
				_cached_company_data[company] = frappe.get_doc("Company", company)

			company_doc = _cached_company_data[company]
			_cached_item_accounts[item_code] = company_doc.default_income_account
		except Exception as e:
			frappe.log_error(
				f"Error fetching income account for {item_code}: {e!s}",
				"Income Account Error",
			)
			_cached_item_accounts[item_code] = None

	return _cached_item_accounts[item_code]


def get_expense_accounts(item_code):
	"""Optimized expense account getter with caching"""
	global _cached_item_accounts

	cache_key = f"{item_code}_expense"
	if cache_key not in _cached_item_accounts:
		try:
			pos_profile = get_current_pos_profile()
			company = pos_profile.company

			# Cache company data
			if company not in _cached_company_data:
				_cached_company_data[company] = frappe.get_doc("Company", company)

			company_doc = _cached_company_data[company]
			_cached_item_accounts[cache_key] = company_doc.default_expense_account
		except Exception as e:
			frappe.log_error(
				f"Error fetching expense account for {item_code}: {e!s}",
				"Expense Account Error",
			)
			_cached_item_accounts[cache_key] = None

	return _cached_item_accounts[cache_key]


from frappe.model.mapper import get_mapped_doc


# Long enough for any note a cashier types; short enough that a pasted page does not land on
# every printed invoice.
REMARKS_MAX_LENGTH = 2000


def _parse_remarks(data):
	"""The checkout's remarks as plain text, or None when there are none.

	Markup is taken out as markup only where there is a tag, so "Price < 100 and qty > 2"
	keeps its words; any angle bracket left is dropped, so no tag can survive into a print.
	"""
	raw = data.get("remarks") if isinstance(data, dict) else None
	if not isinstance(raw, str):
		return None
	if re.search(r"<[A-Za-z/!][^>]*>", raw):
		raw = strip_html_tags(raw)
	text = re.sub(r"[ \t]{2,}", " ", raw.replace("<", "").replace(">", "")).strip()
	return text[:REMARKS_MAX_LENGTH] or None


def _apply_remarks(doc, data):
	"""Write the checkout's remarks onto an invoice or held order whenever the cart sends
	the key; an empty one clears them. A site's own shorter `remarks` field is respected."""
	if not isinstance(data, dict) or "remarks" not in data:
		return
	field = doc.meta.get_field("remarks")
	if not field:
		return
	remarks = _parse_remarks(data)
	if remarks and field.fieldtype not in ("Small Text", "Text", "Long Text", "Text Editor"):
		remarks = remarks[: cint(field.length) or 140]
	doc.remarks = remarks or None


def _stamp_return_with_refunding_shift(return_doc):
	"""The return counts in the shift it happens in - the cash leaves that drawer, and a
	closed shift is never changed after the fact - and belongs to that shift's till.

	Built from the original, the note kept the sale's till: refunded at "Allparts Cashier"
	for a sale rung on "Allparts Sales", it was counted in the Cashier shift yet labelled
	"Allparts Sales", so per-till figures put the refund on a drawer that never paid it and
	the Cashier's own lists left out a note their totals included.
	"""
	current_opening_entry = get_current_pos_opening_entry()
	if not current_opening_entry:
		return
	return_doc.custom_pos_opening_entry = current_opening_entry
	till = frappe.db.get_value("POS Opening Entry", current_opening_entry, "pos_profile")
	# Another company's till never lends the note its name.
	if till and frappe.db.get_value("POS Profile", till, "company") == return_doc.company:
		return_doc.pos_profile = till


@frappe.whitelist()
def return_sales_invoice(invoice_name):
	try:
		_ensure_return_allowed()

		original_invoice = frappe.get_doc("Sales Invoice", invoice_name)
		_ensure_may_return(original_invoice)

		if original_invoice.docstatus != 1:
			frappe.throw("Only submitted invoices can be returned.")

		if original_invoice.is_return:
			frappe.throw("This invoice is already a return.")

		# Exclude payment mapping
		return_doc = get_mapped_doc(
			"Sales Invoice",
			invoice_name,
			{
				"Sales Invoice": {
					"doctype": "Sales Invoice",
					"field_map": {"name": "return_against"},
					"validation": {"docstatus": ["=", 1]},
				},
				"Sales Invoice Item": {
					"doctype": "Sales Invoice Item",
					# ERPNext's return check finds the sold line (and what is already back) by this.
					"field_map": {"name": "sales_invoice_item"},
				},
			},
		)

		return_doc.is_return = 1
		_negate_actual_charges(return_doc)
		return_doc.posting_date = frappe.utils.nowdate()
		_apply_klik_invoice_flags(return_doc, is_held=False, is_submitted=True)

		# The mapper copies the advances table row for row, but Sales Invoice Advance marks
		# its reference fields no_copy: an invoice settled by advances hands the credit note
		# a row pointing at nothing, which ERPNext refuses to save. The money stays on the
		# original's Payment Entry - the credit note inherits none of it.
		return_doc.set("advances", [])
		return_doc.total_advance = 0

		for item in return_doc.items:
			item.qty = -abs(item.qty)
		_negate_order_discount(return_doc, original_invoice)

		_stamp_return_with_refunding_shift(return_doc)

		# Only cash the drawer kept goes back, in one Cash row. A credit sale refunds nothing,
		# and card or M-Pesa money stays on the note as credit for accounts to refund.
		refundable_cash = _get_refundable_cash(original_invoice, return_doc)
		refunded_cash = 0.0

		return_doc.payments = []
		cash_row = _cash_row(original_invoice)
		if cash_row and refundable_cash > 0:
			return_doc.append(
				"payments",
				{
					"mode_of_payment": cash_row.mode_of_payment,
					"amount": -abs(refundable_cash),
					"account": cash_row.account,
				},
			)
			refunded_cash = refundable_cash

		return_doc.calculate_taxes_and_totals()

		precision = return_doc.precision("grand_total") or 2

		# Whatever was not refunded in cash is credit; allocate it against the original invoice.
		_allocate_return_against_original(return_doc, original_invoice, refunded_cash)

		return_doc.save(ignore_permissions=True)
		_carry_tax_id_from_original(return_doc, original_invoice)
		_enforce_submit_permission(return_doc)
		return_doc.submit()

		return {
			"success": True,
			"return_invoice": return_doc.name,
			"credit": _voucher_of(return_doc, precision),
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Return Invoice Error")
		return {"success": False, "message": str(e)}


def _needs_shift_check(doc):
	"""ERPNext's own shift check applies to klik POS sales: not returns, not credit
	sales (is_pos=0), not invoices made outside klik, and not the worker finishing a
	sale that was checked when it was queued."""
	return bool(
		doc.get("custom_is_created_from_klik")
		and doc.pos_profile
		and doc.is_pos
		and not doc.is_return
		and not frappe.flags.get("klik_processing_queued_invoice")
	)


class CustomSalesInvoice(SalesInvoice):
	def validate_pos_opening_entry(self):
		"""ERPNext's check, except that a till without 'Require Daily Shift Close' keeps
		selling in a shift opened on an earlier day."""
		from klik_pos.api.shift import requires_daily_close

		if requires_daily_close(self.pos_profile):
			return super().validate_pos_opening_entry()

		open_shifts = frappe.db.count("POS Opening Entry", {"pos_profile": self.pos_profile, "status": "Open"})
		if not open_shifts:
			frappe.throw(
				title=_("POS Opening Entry Missing"),
				msg=_("No open POS Opening Entry found for POS Profile {0}.").format(
					frappe.bold(self.pos_profile)
				),
			)
		if open_shifts > 1:
			frappe.throw(
				title=_("Multiple POS Opening Entry"),
				msg=_(
					"POS Profile - {0} has multiple open POS Opening Entries. Please close or cancel the existing entries before proceeding."
				).format(self.pos_profile),
			)

	def set_pos_fields(self, for_validate=False):
		# ERPNext resets selling_price_list from customer -> group -> till on every pass,
		# which threw away the list a klik cart was priced from (POS-06134). The minimum
		# selling price judges a till sale against that list, so a klik sale keeps the one
		# build_sales_invoice_doc recorded (resolve_selling_price_list already checked it).
		recorded = self.selling_price_list if self.get("custom_is_created_from_klik") else None
		pos = self._klik_set_pos_fields(for_validate)
		if recorded:
			self.selling_price_list = recorded
		return pos

	def _klik_set_pos_fields(self, for_validate=False):
		# A return carries the taxes it was built with. It takes the refunding till (see
		# _stamp_return_with_refunding_shift), and ERPNext fills an empty taxes_and_charges
		# from the till and then pulls that template's rows into an empty taxes table - so
		# an untaxed sale returned at a taxed till was credited tax it never charged.
		if not (self.is_return and self.return_against):
			return super().set_pos_fields(for_validate)
		template, taxes = self.taxes_and_charges, list(self.get("taxes") or [])
		pos = super().set_pos_fields(for_validate)
		self.taxes_and_charges = template
		self.set("taxes", taxes)
		return pos

	def calculate_taxes_and_totals(self):
		super().calculate_taxes_and_totals()
		# A return applied to its original books its Debtors legs - credit and refund alike -
		# against the original, so the return itself owes nothing. ERPNext zeroes this only
		# for non-POS returns; a POS return kept -grand_total and its credit read twice.
		if self.is_return and self.return_against and self.is_pos and not self.update_outstanding_for_self:
			self.outstanding_amount = 0

	def before_submit(self):
		from klik_pos.api.mpesa import assert_mpesa_rows_backed

		assert_mpesa_rows_backed(self)

		if _needs_shift_check(self):
			# ERPNext only runs this from validate_created_using_pos, which klik never
			# reaches (it never sets is_created_using_pos). Call it directly so a klik
			# sale still needs exactly one Open shift on its till, opened today unless
			# the till does not require a daily close.
			self.validate_pos_opening_entry()

		# Only a sale that went through the queue has a queue status to finish - Queued or
		# Failed and now submitted, whichever way; one submitted directly has none.
		if self.get("queue_status"):
			_update_queue_fields(self, QUEUE_STATUSES["submitted"], error_message=None)
		if _should_reserve_stock(self):
			_cancel_sales_invoice_reservations(self.name)
		self.validate_reserved_stock_availability()
		self.validate_full_payment()

	def on_cancel(self):
		super().on_cancel()
		# A consumed Mpesa C2B Payment Register row points at this invoice through a Link
		# field and stays submitted: it is the record that the receipt was applied here.
		# Frappe refuses to cancel a document that another submitted document links to,
		# which made an M-Pesa-settled sale impossible to cancel at all. Exempt that
		# doctype the way ERPNext exempts its own ledgers just above; the Payment Entry
		# is released by ERPNext's unlink and the money stays on the books as credit.
		# The exemption is doctype-wide, not field-wide: today the only field on that
		# doctype pointing at a Sales Invoice is `sales_invoice`, which is exactly this link.
		self.ignore_linked_doctypes = (
			*(self.ignore_linked_doctypes or ()),
			"Mpesa C2B Payment Register",
		)

	def validate_reserved_stock_availability(self):
		if not _should_reserve_stock(self):
			return
		if not self.update_stock or getattr(self, "is_return", 0):
			return

		item_codes = list({row.item_code for row in self.items if row.item_code})
		item_meta_map = {}
		if item_codes:
			item_meta_map = {
				row.name: row
				for row in frappe.get_all(
					"Item",
					filters={"name": ["in", item_codes]},
					fields=["name", "is_stock_item", "allow_negative_stock"],
				)
			}

		own_reserved_map = _get_sales_invoice_reservation_map(self.name)

		for row in self.items:
			if not row.item_code or not row.warehouse:
				continue
			item_meta = item_meta_map.get(row.item_code)
			# Skip non-stock items and items allowed to go negative (no reservation enforced).
			if item_meta and not int(item_meta.is_stock_item or 0):
				continue
			if item_meta and negative_stock_allowed(item_meta.allow_negative_stock):
				continue

			required_qty = flt(abs(getattr(row, "stock_qty", 0) or 0))
			if required_qty <= 0:
				required_qty = flt(abs(getattr(row, "qty", 0) or 0))
			if required_qty <= 0:
				continue

			actual_qty = flt(
				frappe.db.get_value(
					"Bin",
					{"item_code": row.item_code, "warehouse": row.warehouse},
					"actual_qty",
				)
				or 0
			)
			reserved_map = get_reserved_stock_map(item_codes=[row.item_code], warehouse=row.warehouse)
			reserved_qty = flt(reserved_map.get((row.item_code, row.warehouse), 0))
			available_qty = flt(actual_qty - reserved_qty + flt(own_reserved_map.get((row.item_code, row.warehouse), 0)))

			if required_qty > available_qty + 1e-9:
				frappe.throw(
					_(
						"Reserved stock protection: item {0} in warehouse {1} has only {2} available after reservations, but {3} is required."
					).format(
						frappe.bold(row.item_code),
						frappe.bold(row.warehouse),
						flt(available_qty),
						flt(required_qty),
					)
				)

	def validate_full_payment(self):
		if not self.pos_profile or getattr(self, "is_return", 0):
			return

		allow_partial_payment = frappe.db.get_value(
			"POS Profile", self.pos_profile, "allow_partial_payment"
		)
		allow_partial_payment = allow_partial_payment or getattr(self, "custom_allow_partial_payment", 0)
		precision = self.precision("rounded_total")
		if precision is None:
			precision = self.precision("grand_total")
		invoice_total = flt(self.rounded_total, precision) or flt(self.grand_total, precision)
		# Money that arrived as an advance from a Payment Entry - every M-Pesa receipt now -
		# is paid, even though ERPNext keeps it out of paid_amount.
		paid_amount = flt(flt(self.paid_amount) + flt(self.total_advance), precision)
		# A voucher (customer credit) settles the sale right after submit, so it counts as
		# paid here. Checkout sets the marker only for validated vouchers that cover the sale.
		paid_amount = flt(paid_amount + flt(getattr(self, "_klik_customer_credit", 0)), precision)

		# A credit sale takes no money at the till by design (loyalty points may still be
		# redeemed on it) and always has a named customer. A till that allows credit sales
		# lets such an invoice through without allowing part payment on every other sale; an
		# unpaid walk-in invoice is not a credit sale and is still refused.
		if (
			not allow_partial_payment
			and paid_amount == 0
			and self.customer
			and not _is_walkin_customer(self.customer)
			and _credit_sales_allowed(frappe.get_cached_doc("POS Profile", self.pos_profile))
		):
			return

		if paid_amount < invoice_total and flt(getattr(self, "loyalty_amount", 0)):
			paid_amount = flt(paid_amount + flt(self.loyalty_amount, precision), precision)

		if not allow_partial_payment and paid_amount < invoice_total:
			frappe.throw(
				msg=_("Partial Payment in POS Transactions are not allowed."),
				exc=PartialPaymentValidationError,
			)


def create_payment_entry(sales_invoice, mode_of_payment, amount_paid):
	"""
	Create Payment Entry for B2B Sales Invoice
	"""
	try:
		# Get company and customer details
		company = sales_invoice.company
		customer = sales_invoice.customer

		# Create Payment Entry
		payment_entry = frappe.new_doc("Payment Entry")
		payment_entry.payment_type = "Receive"
		payment_entry.party_type = "Customer"
		payment_entry.party = customer
		payment_entry.company = company
		payment_entry.posting_date = frappe.utils.nowdate()

		# Set paid amount
		payment_entry.paid_amount = amount_paid
		payment_entry.received_amount = amount_paid
		payment_entry.source_exchange_rate = 1
		payment_entry.target_exchange_rate = 1

		company_doc = frappe.get_doc("Company", company)

		payment_entry.party_account = get_customer_receivable_account(customer, company)

		# Handle multiple payment methods
		if isinstance(mode_of_payment, list) and len(mode_of_payment) > 0:
			first_payment = mode_of_payment[0]
			mode_of_payment_doc = frappe.get_doc("Mode of Payment", first_payment["method"])

			for account in mode_of_payment_doc.accounts:
				if account.company == company:
					payment_entry.paid_to = account.default_account
					break

			if not payment_entry.paid_to:
				payment_entry.paid_to = company_doc.default_cash_account

			payment_entry.mode_of_payment = first_payment["method"]

			payment_entry.append(
				"references",
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": sales_invoice.name,
					"allocated_amount": amount_paid,
				},
			)

		else:
			payment_entry.paid_to = company_doc.default_cash_account
			payment_entry.mode_of_payment = "Cash"

			payment_entry.append(
				"references",
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": sales_invoice.name,
					"allocated_amount": amount_paid,
				},
			)

		payment_entry.paid_from_account_currency = sales_invoice.currency
		payment_entry.paid_to_account_currency = sales_invoice.currency

		payment_entry.save()
		payment_entry.submit()

		return payment_entry

	except Exception as e:
		frappe.log_error(
			frappe.get_traceback(),
			f"Error creating payment entry for invoice {sales_invoice.name}",
		)
		frappe.throw(f"Failed to create payment entry: {e!s}")


def get_customer_receivable_account(customer, company):
	"""Get customer's receivable account using ERPNext utility"""
	try:
		from erpnext.accounts.party import get_party_account

		return get_party_account("Customer", customer, company)
	except Exception as e:
		frappe.log_error(f"Error getting receivable account for customer {customer}: {e!s}")
		return frappe.db.get_value("Company", company, "default_receivable_account")


@frappe.whitelist()
def returned_qty(customer, sales_invoice, item):
	"""
	Get total returned quantity for a specific item (item_code) against a given sales invoice.
	- sales_invoice should be the original invoice name.
	- item should be the item_code (not item name or child row name).
	Returns: {'total_returned_qty': <float>}
	"""
	values = {
		"customer": customer,
		"sales_invoice": sales_invoice,
		"item": item,
	}

	# Sum qty from Sales Invoice Items of return invoices that point to the original invoice
	result = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(sii.qty), 0) AS total_returned_qty
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
		WHERE si.is_return = 1
		  AND si.return_against = %(sales_invoice)s
		  AND sii.item_code = %(item)s
		  AND si.docstatus = 1
		  AND si.customer = %(customer)s
		""",
		values=values,
		as_dict=True,
	)

	total = abs(result[0]["total_returned_qty"]) if result else 0.0
	return {
		"total_returned_qty": round(float(total), 6)
	}  # Round to 6 decimal places to avoid precision issues


@frappe.whitelist()
def get_valid_sales_invoices(doctype, txt, searchfield, start, page_len, filters=None):
	"""Get valid sales invoices based on filters for multi-invoice returns"""
	_ensure_return_allowed()

	filters = filters or {}

	customer = filters.get("customer")
	shipping_address = filters.get("shipping_address")
	item_code = filters.get("item_code")
	start_date = filters.get("start_date")

	if not customer or not item_code or not start_date:
		return []

	# Build dynamic conditions
	conditions = [
		"si.docstatus = 1",
		"si.is_return = 0",
		"si.custom_pos_opening_entry IS NOT NULL AND si.custom_pos_opening_entry != ''",
	]
	query_params = {
		"txt": f"%{txt}%",
		"start": start,
		"page_len": page_len,
	}

	if customer:
		conditions.append("si.customer = %(customer)s")
		query_params["customer"] = customer

	if shipping_address:
		conditions.append("si.shipping_address_name = %(shipping_address)s")
		query_params["shipping_address"] = shipping_address

	if item_code:
		conditions.append("sii.item_code = %(item_code)s")
		query_params["item_code"] = item_code

	if start_date:
		conditions.append("si.posting_date >= %(start_date)s")
		query_params["start_date"] = start_date

	conditions.append(
		"""
		(sii.qty + COALESCE((
			SELECT SUM(cd.qtr)
			FROM `tabCredit Details` cd
			JOIN `tabSales Invoice` rsi ON cd.parent = rsi.name
			WHERE cd.sales_invoice = si.name
			AND cd.item = sii.item_code
			AND rsi.customer = si.customer
			AND rsi.docstatus = 1
			AND rsi.status != 'Cancelled'
		), 0)) > 0
	"""
	)

	where_clause = " AND ".join(conditions)
	query = f"""
		SELECT DISTINCT si.name,si.posting_date,sii.qty
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
		WHERE {where_clause}
		AND si.name LIKE %(txt)s
		LIMIT %(start)s, %(page_len)s
	"""

	return frappe.db.sql(query, query_params)


def _returnable_scope(filters):
	"""What a till may refund: the same invoices it may open (_may_read_row) - one's own, and
	with the till's flag on, others' rung on a till selling from its warehouse. A sale from
	another branch would put its stock back in that branch's warehouse while the cash left
	this drawer. Narrows `filters` in place; returns or_filters for frappe.get_all."""
	try:
		pos_doc = get_current_pos_profile()
	except Exception:
		pos_doc = None
	if pos_doc and _is_invoice_manager():
		return [["owner", "=", frappe.session.user], ["company", "=", pos_doc.company]]
	if not pos_doc or not _profile_allows_other_cashiers(pos_doc):
		filters["owner"] = frappe.session.user
		return None
	# One's own anywhere; the shared tills are all of this till's company already.
	return [["owner", "=", frappe.session.user], ["pos_profile", "in", _tills_sharing_warehouse(pos_doc)]]


def _ensure_may_return(original_invoice):
	if not _may_read_invoice(original_invoice):
		frappe.throw(
			_("Invoice {0} was rung on another branch's till; return it there.").format(original_invoice.name),
			frappe.PermissionError,
		)


@frappe.whitelist()
def get_customer_invoices_for_return(customer, start_date=None, end_date=None, shipping_address=None):
	"""Get all invoices for a customer within date range that can be returned"""
	try:
		_ensure_return_allowed()

		filters = {
			"customer": customer,
			"docstatus": 1,
			"is_return": 0,
			"status": ["!=", "Cancelled"],
			"custom_pos_opening_entry": ["!=", ""],
		}
		or_filters = _returnable_scope(filters)

		if start_date:
			filters["posting_date"] = [">=", start_date]
		if end_date:
			if "posting_date" in filters:
				filters["posting_date"] = ["between", [start_date, end_date]]
			else:
				filters["posting_date"] = ["<=", end_date]

		# Add shipping address filter if provided
		if shipping_address:
			filters["customer_address"] = shipping_address

		invoices = frappe.get_all(
			"Sales Invoice",
			filters=filters,
			or_filters=or_filters,
			fields=[
				"name",
				"posting_date",
				"posting_time",
				"customer",
				"grand_total",
				"paid_amount",
				"status",
				"outstanding_amount",
				"total_advance",
				"change_amount",
				"is_pos",
			],
			order_by="posting_date desc",
		)

		# Batch fetch all items for all invoices
		invoice_names = [inv.name for inv in invoices]
		all_items = []
		if invoice_names:
			all_items = frappe.get_all(
				"Sales Invoice Item",
				filters={"parent": ["in", invoice_names]},
				fields=["parent", "item_code", "item_name", "qty", "rate", "amount"],
				order_by="parent, idx",
			)

		# Batch fetch all returned quantities for all items at once
		returned_qty_map = {}
		if all_items:
			item_codes = list(set([item.item_code for item in all_items]))
			_invoice_item_pairs = [(item.parent, item.item_code) for item in all_items]

			if item_codes:
				invoice_placeholders, invoice_params = _sql_in_clause(invoice_names)
				item_placeholders, item_params = _sql_in_clause(item_codes)
				# Create a more efficient query to get all returned quantities
				returns_query = """
					SELECT
						rsi.return_against as original_invoice,
						sii.item_code,
						COALESCE(SUM(ABS(sii.qty)), 0) as total_returned_qty
					FROM `tabSales Invoice` rsi
					JOIN `tabSales Invoice Item` sii ON rsi.name = sii.parent
					WHERE rsi.is_return = 1
					  AND rsi.return_against IN ({})
					  AND sii.item_code IN ({})
					  AND rsi.docstatus = 1
					  AND rsi.customer = %s
					GROUP BY rsi.return_against, sii.item_code
				""".format(
					invoice_placeholders,
					item_placeholders,
				)

				returns_data = frappe.db.sql(
					returns_query, (*invoice_params, *item_params, customer), as_dict=True
				)
				returned_qty_map = {
					(row.original_invoice, row.item_code): row.total_returned_qty for row in returns_data
				}

		# Group items by invoice and calculate returned quantities
		invoice_items_map = {}
		for item in all_items:
			if item.parent not in invoice_items_map:
				invoice_items_map[item.parent] = []

			returned_qty_value = returned_qty_map.get((item.parent, item.item_code), 0)
			item.returned_qty = returned_qty_value
			item.available_qty = round(
				item.qty - returned_qty_value, 6
			)  # Round to 6 decimal places to avoid precision issues

			invoice_items_map[item.parent].append(item)

		# Read the same way Invoice History and the detail page read it, so the three
		# surfaces cannot disagree about how an invoice was paid. This replaces a
		# Payment-Entry fallback that only looked for entries when the payments table was
		# empty: M-Pesa money arrives as an advance, on an invoice whose payments table has
		# the mode in it with no amount, so that fallback never fired for it.
		payment_methods_map = _batch_fetch_payment_methods(invoice_names)
		fixed_charges_map = _fixed_charges_for(invoice_names)
		reversed_fees_map = _reversed_fixed_charges(invoice_names)

		# Assign items to invoices
		for invoice in invoices:
			invoice.items = invoice_items_map.get(invoice.name, [])
			# A Shipping Rule's courier fee is a line the cashier can tick, like an item;
			# reversed_by names the credit note that already took it back, if any.
			reversed_by = reversed_fees_map.get(invoice.name, {})
			invoice.fixed_charges = [
				{**charge, "reversed_by": reversed_by.get(charge["account_head"])}
				for charge in fixed_charges_map.get(invoice.name, [])
			]

			payment_methods = payment_methods_map.get(invoice.name, [])
			invoice.payment_methods = payment_methods
			# What the till may hand back in cash for this sale - the same ceiling a return
			# applies - so the picker never offers card or M-Pesa money as a cash refund.
			invoice.refundable_cash = _get_refundable_cash(
				frappe._dict(
					name=invoice.name,
					grand_total=invoice.get("grand_total"),
					outstanding_amount=invoice.get("outstanding_amount"),
					total_advance=invoice.get("total_advance"),
					change_amount=invoice.get("change_amount"),
					payments=[frappe._dict(r) for r in payment_methods if not r.get("payment_entry")],
				),
				frappe._dict(is_pos=invoice.get("is_pos")),
			)
			# Keep backward compatibility - the same label Invoice History shows.
			invoice.payment_method = mode_label(payment_methods)

		return {"success": True, "data": invoices}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Error fetching customer invoices for return")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def create_partial_return(
	invoice_name,
	return_items,
	payment_method=None,
	return_amount=None,
	expected_return_amount=None,
	return_fixed_charges=None,
):
	"""Create a partial return for selected items from an invoice with custom payment method.

	``return_fixed_charges`` is the cashier's tick for the courier fee (a Shipping Rule's
	"Actual" row): 1 reverses it on the credit note, 0 leaves it off. A till that sends
	nothing gets the old rule - the fee comes back only with every quantity on the invoice.
	"""

	try:
		_ensure_return_allowed()

		if isinstance(return_items, str):
			return_items = json.loads(return_items)

		original_invoice = frappe.get_doc("Sales Invoice", invoice_name)
		_ensure_may_return(original_invoice)

		if original_invoice.docstatus != 1:
			frappe.throw("Only submitted invoices can be returned.")

		if original_invoice.is_return:
			frappe.throw("This invoice is already a return.")

		# Create return invoice using the same approach as return_sales_invoice
		return_doc = get_mapped_doc(
			"Sales Invoice",
			invoice_name,
			{
				"Sales Invoice": {
					"doctype": "Sales Invoice",
					"field_map": {"name": "return_against"},
					"validation": {"docstatus": ["=", 1]},
				},
				"Sales Invoice Item": {
					"doctype": "Sales Invoice Item",
					# ERPNext's return check finds the sold line (and what is already back) by this.
					"field_map": {"name": "sales_invoice_item"},
				},
			},
		)

		return_doc.is_return = 1
		_negate_actual_charges(return_doc)
		return_doc.posting_date = frappe.utils.nowdate()
		_stamp_delivery_date(return_doc)
		_apply_klik_invoice_flags(return_doc, is_held=False, is_submitted=True)

		# The mapper copies the advances table row for row, but Sales Invoice Advance marks
		# its reference fields no_copy: an invoice settled by advances hands the credit note
		# a row pointing at nothing, which ERPNext refuses to save. The money stays on the
		# original's Payment Entry - the credit note inherits none of it.
		return_doc.set("advances", [])
		return_doc.total_advance = 0

		_stamp_return_with_refunding_shift(return_doc)

		# Filter items to only include selected ones with return quantities
		filtered_items = []
		for return_item in return_items:
			if return_item.get("return_qty", 0) > 0:
				# The sale's line by its row name when the till sends it: one item may sit on two
				# lines at two prices. Item code alone is an older till's request.
				line = return_item.get("name")
				for item in return_doc.items:
					if (item.sales_invoice_item == line) if line else (item.item_code == return_item["item_code"]):
						item.qty = -abs(return_item["return_qty"])
						filtered_items.append(item)
						break

		return_doc.items = filtered_items
		_negate_order_discount(return_doc, original_invoice)
		if return_fixed_charges is None:
			reverse_fee = _returns_every_line(original_invoice, return_doc) and not _reversed_fixed_charges(
				[invoice_name]
			).get(invoice_name)
		else:
			reverse_fee = bool(cint(return_fixed_charges))
			if reverse_fee:
				_refuse_second_fee_reversal(invoice_name)
		if not reverse_fee:
			_drop_actual_charges(return_doc)

		# Clear existing payments
		return_doc.payments = []

		# A partial note credits exactly what came back (the 617.50 the dialog showed); only the
		# return that finishes the sale rounds. Rounded one by one, a sale's notes added up past
		# what was paid (2899 back on 2898).
		if not _finishes_the_sale(original_invoice, return_doc):
			return_doc.disable_rounded_total = 1

		# Calculate total returned amount (baseline expected refund)
		# Prefer client-provided expected amount; fallback to the credit note's own total, which
		# carries the discount and taxes a bare qty * rate would miss.
		return_doc.calculate_taxes_and_totals()
		credit_total = abs(flt(return_doc.rounded_total or return_doc.grand_total))
		if expected_return_amount is not None:
			try:
				total_returned_amount = flt(expected_return_amount, return_doc.precision("grand_total") or 2)
			except Exception:
				total_returned_amount = credit_total
		else:
			total_returned_amount = credit_total

		final_return_amount = return_amount if return_amount is not None else total_returned_amount
		# The till offers the unrounded total (399.90); the note settles at its rounded one (400).
		# Refunding the former would leave the rounding difference open as a stray voucher.
		if abs(flt(final_return_amount) - abs(flt(return_doc.grand_total))) < 0.005:
			final_return_amount = credit_total

		# No mode given: refund through the sale's own cash mode - not a mode assumed to be
		# named "Cash", which a till may not have.
		sale_cash_row = _cash_row(original_invoice)
		final_payment_method = payment_method or (sale_cash_row.mode_of_payment if sale_cash_row else "Cash")

		# Cash back is capped at what the customer actually handed over. A credit sale refunds
		# nothing: the return then carries no payment rows, so the credit note keeps a real
		# outstanding balance instead of silently netting itself to zero.
		refundable_cash = _get_refundable_cash(original_invoice, return_doc)
		refunded_cash = min(flt(final_return_amount), refundable_cash)
		if refunded_cash < 0:
			refunded_cash = 0.0

		# Optionally persist the auto-calculated expected refund if a custom field exists
		try:
			_si_meta = frappe.get_meta("Sales Invoice")
			if any(df.fieldname == "custom_expected_refund_amount" for df in _si_meta.fields):
				return_doc.custom_expected_refund_amount = flt(
					total_returned_amount, return_doc.precision("grand_total") or 2
				)
		except Exception:
			pass

		if refunded_cash > 0:
			if not _is_cash_mode(final_payment_method):
				frappe.throw(
					_(
						"Only cash can be refunded at the till. {0} money goes back through accounts as a Payment Entry reversal; choose a cash mode or refund nothing."
					).format(final_payment_method)
				)
			return_doc.append(
				"payments",
				{
					"mode_of_payment": final_payment_method,
					"amount": -abs(refunded_cash),
				},
			)

		# Recalculate totals (payment amount stays as user entered)
		try:
			return_doc.calculate_taxes_and_totals()
		except Exception:
			pass

		# Whatever was not refunded in cash is credit; allocate it against the original invoice.
		_allocate_return_against_original(return_doc, original_invoice, refunded_cash)

		return_doc.save(ignore_permissions=True)
		_carry_tax_id_from_original(return_doc, original_invoice)
		_enforce_submit_permission(return_doc)
		return_doc.submit()

		precision = return_doc.precision("grand_total") or 2
		credited_amount = flt(
			abs(flt(return_doc.rounded_total or return_doc.grand_total)) - flt(refunded_cash), precision
		)

		# Credit kept on the note is the customer's voucher; otherwise it paid down the invoice.
		credit_went = (
			_("left as store credit on {0}").format(return_doc.name)
			if cint(return_doc.update_outstanding_for_self)
			else _("credited to the invoice")
		)
		def money(amount):
			return fmt_money(amount, precision, return_doc.currency)

		if refunded_cash > 0 and credited_amount > 0:
			message = _("Return created: {0} (refunded {1} via {2}, {3} {4})").format(
				return_doc.name, money(refunded_cash), final_payment_method, money(credited_amount), credit_went
			)
		elif refunded_cash > 0:
			message = _("Return created: {0} (refunded {1} via {2})").format(
				return_doc.name, money(refunded_cash), final_payment_method
			)
		else:
			message = _("Return created: {0} ({1} {2}, no cash refunded)").format(
				return_doc.name, money(credited_amount), credit_went
			)

		return {
			"success": True,
			"return_invoice": return_doc.name,
			"refunded_amount": flt(refunded_cash, precision),
			"credited_amount": credited_amount,
			"payment_method": final_payment_method if refunded_cash > 0 else None,
			"credit": _voucher_of(return_doc, precision),
			"message": message,
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Partial Return Error")
		return {"success": False, "message": str(e)}


def _voucher_of(return_doc, precision):
	"""The store-credit voucher a return leaves - its own number, the original sale's and
	what it holds - or None when nothing was left on the note."""
	available = flt(-return_doc.outstanding_amount, precision)
	if available <= 0:
		return None
	return {"note": return_doc.name, "original": return_doc.return_against, "available": available}


@frappe.whitelist()
def create_multi_invoice_return(return_data):
	"""Create multiple return invoices for items from different invoices"""
	try:
		_ensure_return_allowed()

		if isinstance(return_data, str):
			return_data = json.loads(return_data)

		invoice_returns = return_data.get("invoice_returns", [])

		created_returns = []
		failed = []

		for _i, invoice_return in enumerate(invoice_returns):
			invoice_name = invoice_return.get("invoice_name")
			return_items = invoice_return.get("return_items", [])
			payment_method = invoice_return.get("payment_method")
			return_amount = invoice_return.get("return_amount")

			if return_items:
				# Call create_partial_return with payment method and return amount
				result = create_partial_return(
					invoice_name,
					return_items,
					payment_method=payment_method,
					return_amount=return_amount,
					return_fixed_charges=invoice_return.get("return_fixed_charges"),
				)
				if result.get("success"):
					created_returns.append(result.get("return_invoice"))
				else:
					failed.append({"invoice_name": invoice_name, "message": result.get("message")})

		# Every return that did not go through is named: reporting success while some quietly
		# failed left the cashier believing goods were returned that were not.
		if failed:
			message = _("Created {0} return(s); {1} failed: {2}").format(
				len(created_returns),
				len(failed),
				"; ".join(f"{f['invoice_name']}: {f['message']}" for f in failed),
			)
		else:
			message = _("Created {0} return invoices successfully").format(len(created_returns))
		return {
			"success": not failed,
			"created_returns": created_returns,
			"failed": failed,
			"message": message,
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Multi Invoice Return Error")
		return {"success": False, "message": str(e)}


def delete_draft_invoices_for_opening_entry(opening_entry_name):
	"""
	Delete all draft Sales Invoices linked to the given POS Opening Entry (session).
	Called on POS close when POS Profile has custom_clear_draft_invoices enabled.
	"""
	try:
		drafts = frappe.get_all(
			"Sales Invoice",
			filters={
				"docstatus": 0,
				"custom_pos_opening_entry": opening_entry_name,
			},
			pluck="name",
		)
		deleted = 0
		for name in drafts:
			try:
				doc = frappe.get_doc("Sales Invoice", name)
				if doc.docstatus == 0:
					_cancel_sales_invoice_reservations(doc.name)
					doc.delete()
					deleted += 1
			except Exception as e:
				frappe.logger().error(f"Error deleting draft invoice {name}: {e}")
		if deleted:
			frappe.logger().info(f"Cleared {deleted} draft invoice(s) for opening entry {opening_entry_name}")
		return deleted
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Clear draft invoices on POS close")
		# Do not raise - closing entry already succeeded
		return 0


def _live_stk_request(invoice_name):
	"""An M-Pesa STK push sent from this invoice that may still pay, or already paid, it.

	The request names the invoice, so a payment arriving after the invoice is deleted has
	nowhere to land. Only a Failed push is safe to forget.
	"""
	if not frappe.db.exists("DocType", "Mpesa Express Request"):
		return None
	return frappe.db.get_value(
		"Mpesa Express Request",
		{
			"reference_doctype": "Sales Invoice",
			"reference_name": invoice_name,
			"status": ["in", ["In Progress", "Completed"]],
		},
		"name",
	)


def _stk_request_sent_response(invoice_name, request_name):
	return {
		"success": False,
		"code": "mpesa_request_sent",
		"invoice_id": invoice_name,
		"error": _(
			"Draft {0} was kept: M-Pesa request {1} was sent from it, and its payment needs this invoice. Finish or cancel it from Invoice History."
		).format(invoice_name, request_name),
	}


@frappe.whitelist(methods=["POST"])
def discard_mpesa_draft(invoice_id):
	"""Delete the draft M-Pesa checkout made, when the cashier leaves without finishing it.

	M-Pesa needs an invoice before the money moves, so the payment dialog makes this draft
	up front; closing the dialog, or holding the order instead, used to leave it behind.
	Cashiers cannot delete Sales Invoices, so this deletes with permissions ignored and is
	narrow on purpose: only the caller's own POS draft, not a sale queued for the background
	worker, and not one an STK push was sent from.
	"""
	# Decide on a locked read: a submit committing meanwhile must be seen, not a snapshot.
	state = frappe.db.get_value(
		"Sales Invoice",
		invoice_id,
		["docstatus", "owner", "custom_is_created_from_klik", "custom_is_held", "enable_background_invoice_submission"],
		as_dict=True,
		for_update=True,
	)
	if not state:
		return {"success": True, "message": f"Draft invoice {invoice_id} is already gone"}

	if (
		state.docstatus != 0
		or state.owner != frappe.session.user
		or not cint(state.custom_is_created_from_klik)
		# A draft held the old way is a hold, not an M-Pesa checkout's draft.
		or cint(state.custom_is_held)
		or cint(state.enable_background_invoice_submission)
	):
		return {"success": False, "error": _("Draft {0} was kept: it is not this checkout's to discard.").format(invoice_id)}

	live_request = _live_stk_request(invoice_id)
	if live_request:
		return _stk_request_sent_response(invoice_id, live_request)

	invoice_doc = frappe.get_doc("Sales Invoice", invoice_id)
	_check_discardable_links(invoice_doc)
	_cancel_sales_invoice_reservations(invoice_doc.name)
	frappe.delete_doc("Sales Invoice", invoice_doc.name, ignore_permissions=True, force=True)
	return {"success": True, "message": f"Draft invoice {invoice_id} discarded"}


def _check_discardable_links(invoice_doc):
	"""Frappe's delete-time link checks, except a Failed STK request's link to the draft.

	Only Failed requests can still name the draft here, and they paid nothing; frappe would
	otherwise refuse the delete on their account alone. Any other link still refuses it.
	"""
	from frappe.model.delete_doc import (
		check_if_doc_is_linked,
		get_dynamic_linked_docs,
		raise_link_exists_exception,
	)

	check_if_doc_is_linked(invoice_doc)
	for link in get_dynamic_linked_docs(invoice_doc, "Delete"):
		failed_stk = link["reference_doctype"] == "Mpesa Express Request" and (
			frappe.db.get_value("Mpesa Express Request", link["reference_docname"], "status") == "Failed"
		)
		# An active Sales Invoice workflow's open action; frappe's own on_trash clears it.
		if not failed_stk and link["reference_doctype"] != "Workflow Action":
			raise_link_exists_exception(
				invoice_doc, link["reference_doctype"], link["reference_docname"], link["at_position"]
			)


@frappe.whitelist()
def delete_draft_invoice(invoice_id):
	"""
	Delete a draft sales invoice.
	Only allows deletion of Draft status invoices.
	"""
	try:
		# Get the invoice document
		invoice_doc = frappe.get_doc("Sales Invoice", invoice_id)

		if invoice_doc.status != "Draft":
			return {
				"success": False,
				"error": f"Cannot delete invoice {invoice_id}. Only Draft invoices can be deleted. Current status: {invoice_doc.status}",
			}

		live_request = _live_stk_request(invoice_doc.name)
		if live_request:
			return _stk_request_sent_response(invoice_doc.name, live_request)

		_cancel_sales_invoice_reservations(invoice_doc.name)
		invoice_doc.delete()

		return {
			"success": True,
			"message": f"Draft invoice {invoice_id} deleted successfully",
		}

	except frappe.DoesNotExistError:
		return {"success": False, "error": f"Invoice {invoice_id} not found"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), f"Error deleting draft invoice {invoice_id}")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def submit_draft_invoice(invoice_id, data=None, held_order_id=None, remarks=None):
	return _retry_lock_race(_submit_draft_invoice, invoice_id, data, held_order_id, remarks)


def _submit_draft_invoice(invoice_id, data=None, held_order_id=None, remarks=None):
	"""
	Submit a draft sales invoice directly without payment dialog.
	This converts a draft invoice to submitted status.

	held_order_id: the held Sales Order this draft was paid for (M-Pesa makes its draft before
	the money moves). It is finished as checkout_held_order would: locked, access-checked,
	linked, price-approval checked, and taken off the Held tab once the invoice goes through.

	remarks: the checkout's note, applied whether or not `data` comes - an M-Pesa sale paid
	wholly from receipts sends no cart data, and a note typed after picking them must land.
	"""
	# Customer credit (vouchers) works as in the ordinary checkout: checked against the
	# rebuilt draft before submit, settled right after it (or by the queue worker).
	_payload = (json.loads(data) if isinstance(data, str) else data) if data else {}
	credit_rows = []

	draft_touched = False
	try:
		invoice_doc = frappe.get_doc("Sales Invoice", invoice_id)

		if invoice_doc.status != "Draft":
			# code/docstatus let the POS tell "submitted or cancelled elsewhere" from any other
			# failure, and drop its link to this invoice instead of retrying it forever.
			return {
				"success": False,
				"code": "not_draft",
				"invoice_id": invoice_id,
				"docstatus": invoice_doc.docstatus,
				"error": f"Cannot submit invoice {invoice_id}. Only Draft invoices can be submitted. Current status: {invoice_doc.status}",
			}

		# This endpoint reports failure without raising, so the request would commit whatever
		# was saved before it: the rebuilt cart, the background flag, the held order's link.
		# A refused submit leaves the draft as it was.
		frappe.db.savepoint("klik_submit_draft_invoice")
		draft_touched = True

		waiting = frappe.db.exists("DocType", "Mpesa Express Request") and frappe.db.get_value(
			"Mpesa Express Request",
			{"reference_doctype": "Sales Invoice", "reference_name": invoice_id, "status": "In Progress"},
			"name",
		)
		if waiting:
			# Its payment would land on an invoice already finished with other money.
			return {
				"success": False,
				"code": "mpesa_request_waiting",
				"invoice_id": invoice_id,
				"error": _("M-Pesa request {0} for this invoice is still waiting on the customer.").format(waiting),
			}

		if held_order_id:
			from klik_pos.api.sales_order import _claim_held_order

			gone = _claim_held_order(held_order_id)
			if gone:
				return gone
			if invoice_doc.meta.has_field("powerpack_source_order"):
				invoice_doc.powerpack_source_order = held_order_id

		enable_background_submission = bool(invoice_doc.enable_background_invoice_submission)

		if data:
			(
				customer,
				items,
				amount_paid,
				sales_and_tax_charges,
				mode_of_payment,
				business_type,
				roundoff_amount,
				delivery_charge,
				delivery_personnel,
				is_credit_sale,
				allow_partial_payment,
				due_date,
				salesperson,
				tax_id,
				enable_background_submission,
				loyalty_redemption,
			) = parse_invoice_data(data)

			_ef = _parse_extra_fields(data)
			rebuilt_doc = build_sales_invoice_doc(
				customer,
				items,
				amount_paid,
				sales_and_tax_charges,
				mode_of_payment,
				business_type,
				roundoff_amount,
				delivery_charge,
				include_payments=True,
				delivery_personnel=delivery_personnel,
				is_credit_sale=is_credit_sale,
				allow_partial_payment=allow_partial_payment,
				due_date=due_date,
				salesperson=salesperson,
				tax_id=tax_id,
				create_batch_and_serial_bundle=False,
				enable_background_submission=enable_background_submission,
				loyalty_redemption=loyalty_redemption,
				walkin_name=data.get("walkin_name"),
				walkin_phone=data.get("walkin_phone"),
				extra_fields=_ef,
				order_discount_amount=flt(data.get("orderDiscountAmount") or 0),
				shipping_rule=data.get("shipping_rule") or None,
				selling_price_list=data.get("selling_price_list") or None,
			)

			_apply_quote_link(invoice_doc, data)
			_set_customer(invoice_doc, rebuilt_doc.customer)
			_apply_remarks(invoice_doc, data)
			invoice_doc.due_date = rebuilt_doc.due_date
			_stamp_delivery_date(invoice_doc, rebuilt_doc.get("custom_delivery_date"))
			invoice_doc.enable_background_invoice_submission = rebuilt_doc.enable_background_invoice_submission
			invoice_doc.custom_delivery_personnel = rebuilt_doc.custom_delivery_personnel
			invoice_doc.tax_id = rebuilt_doc.tax_id
			_apply_walkin_party_fields(
				invoice_doc,
				walkin_name=rebuilt_doc.get("custom_walkin_customer_name"),
				walkin_phone=rebuilt_doc.get("custom_walkin_phone"),
				walkin_tax_id=rebuilt_doc.get("custom_walkin_tax_id") or rebuilt_doc.tax_id,
			)
			from klik_pos.api.pos_profile import get_configured_extra_fieldnames
			for _fn in get_configured_extra_fieldnames():
				if _fn == SITE_SALE_TYPE_FIELD:
					continue  # fixed with the draft's name; see _set_site_sale_type
				if invoice_doc.meta.has_field(_fn) and rebuilt_doc.meta.has_field(_fn):
					invoice_doc.set(_fn, rebuilt_doc.get(_fn))
			invoice_doc.pos_profile = rebuilt_doc.pos_profile
			# Finished here, it counts in this till's shift - not in the one that started it.
			if rebuilt_doc.get("custom_pos_opening_entry"):
				invoice_doc.custom_pos_opening_entry = rebuilt_doc.custom_pos_opening_entry
			invoice_doc.company = rebuilt_doc.company
			invoice_doc.currency = rebuilt_doc.currency
			invoice_doc.selling_price_list = rebuilt_doc.selling_price_list
			invoice_doc.conversion_rate = rebuilt_doc.conversion_rate
			invoice_doc.update_stock = rebuilt_doc.update_stock
			invoice_doc.warehouse = rebuilt_doc.warehouse
			invoice_doc.cost_center = rebuilt_doc.cost_center
			invoice_doc.is_pos = rebuilt_doc.is_pos
			invoice_doc.redeem_loyalty_points = rebuilt_doc.redeem_loyalty_points
			invoice_doc.loyalty_points = rebuilt_doc.loyalty_points
			invoice_doc.loyalty_amount = rebuilt_doc.loyalty_amount
			invoice_doc.loyalty_program = rebuilt_doc.loyalty_program
			invoice_doc.loyalty_redemption_account = rebuilt_doc.loyalty_redemption_account
			invoice_doc.loyalty_redemption_cost_center = rebuilt_doc.loyalty_redemption_cost_center
			invoice_doc.taxes_and_charges = rebuilt_doc.taxes_and_charges
			invoice_doc.apply_discount_on = rebuilt_doc.apply_discount_on
			invoice_doc.discount_amount = rebuilt_doc.discount_amount
			invoice_doc.shipping_rule = rebuilt_doc.get("shipping_rule")
			invoice_doc.set("items", [])
			for item_row in rebuilt_doc.get("items", []):
				invoice_doc.append("items", item_row.as_dict())
			invoice_doc.set("taxes", [])
			for tax_row in rebuilt_doc.get("taxes", []):
				invoice_doc.append("taxes", tax_row.as_dict())
			invoice_doc.set("sales_team", [])
			for sales_person_row in rebuilt_doc.get("sales_team", []):
				invoice_doc.append("sales_team", sales_person_row.as_dict())

			if items:
				_create_batch_and_serial_bundle(items, invoice_doc)

			invoice_doc.set_taxes()
			# Emptied first: ERPNext refilling the till's modes over rows already here tells the
			# cashier "Payment methods refreshed" - about rows replaced just below anyway.
			invoice_doc.set("payments", [])
			_set_missing_values_keeping_warehouse(invoice_doc)
			invoice_doc.calculate_taxes_and_totals()

			# Payments must be applied after the first totals pass, then totals are recalculated
			# so ERPNext includes both payment rows and loyalty redemption in paid/outstanding amounts.
			invoice_doc.set("payments", [])
			_add_payment_entries(invoice_doc, mode_of_payment)
			receipts = _picked_receipts(_payload)
			if receipts:
				from klik_pos.api.mpesa import attach_mpesa_receipts

				attach_mpesa_receipts(invoice_doc, receipts)
			invoice_doc.calculate_taxes_and_totals()

			invoice_doc.save(ignore_permissions=True)

			credit_rows = validate_allocations(invoice_doc, _payload.get("customerCredit") or [])
			_reserve_checkout_credit(invoice_doc, credit_rows)

		if remarks is not None:
			_apply_remarks(invoice_doc, {"remarks": remarks})

		if held_order_id:
			if not data:
				invoice_doc.save(ignore_permissions=True)
			_refuse_unapproved_price_breach(invoice_doc)

		validate_required_salesperson(invoice_doc)

		if enable_background_submission:
			if _needs_shift_check(invoice_doc):
				# A klik POS sale still needs exactly one Open shift on its till, opened
				# today, before it is queued - the worker skips this check when it
				# finishes the sale, so it must run before anything below marks the
				# invoice Queued or reserves stock for it. Nothing here has written
				# anything yet, so a refusal leaves the draft exactly as it was.
				invoice_doc.validate_pos_opening_entry()

			_mark_invoice_queued(invoice_doc, frappe.session.user)
			_apply_klik_invoice_flags(invoice_doc, is_submitted=False)
			invoice_doc.save(ignore_permissions=True)

			try:
				_reserve_stock_for_queued_invoice(invoice_doc)
			except Exception as reserve_error:
				_raise_lock_race(reserve_error)
				_update_queue_fields(invoice_doc, QUEUE_STATUSES["failed"], error_message=str(reserve_error))
				invoice_doc.save(ignore_permissions=True)
				return {"success": False, "error": str(reserve_error)}

			frappe.enqueue(
				"klik_pos.api.sales_invoice.process_queued_sales_invoice",
				queue="long",
				enqueue_after_commit=True,
				invoice_name=invoice_doc.name,
				requested_by=frappe.session.user,
				customer_credit=credit_rows,
			)

			if held_order_id:
				from klik_pos.api.sales_order import _remove_checked_out_order

				_remove_checked_out_order(held_order_id)

			return {
				"success": True,
				"message": f"Draft invoice {invoice_id} queued for background submission",
				"queue_status": invoice_doc.queue_status,
				"invoice_name": invoice_doc.name,
				"invoice": invoice_doc,
			}
		else:
			# Receipts, advances and the submit are one unit: a failure anywhere in it (stock,
			# a PIN check, the reconciliation) must not leave an entry minted, a receipt
			# linked or advances saved on the draft - this endpoint reports failure without
			# raising, so the request would otherwise commit the half-done checkout.
			frappe.db.savepoint("klik_submit_draft")
			try:
				# Payment-Entry-first M-Pesa: each recorded receipt becomes a Payment Entry and
				# the draft takes what it owes as advances, before submit; the remainder stays
				# unallocated on that same entry and is surfaced after submit as excess.
				mpesa_allocation = None
				if invoice_doc.get("custom_mpesa_reconciled_payments"):
					from klik_pos.api.mpesa import _allocate_receipts_before_submit

					mpesa_allocation = _allocate_receipts_before_submit(invoice_doc)

				_apply_klik_invoice_flags(invoice_doc, is_submitted=True)
				_enforce_submit_permission(invoice_doc)
				invoice_doc.submit()
				try:
					_cancel_sales_invoice_reservations(invoice_doc.name)
				except Exception as e:
					_raise_lock_race(e)
					frappe.log_error(
						frappe.get_traceback(),
						f"Failed to cancel reservations after submit for {invoice_doc.name}",
					)

				mpesa_reconciliation = None
				if invoice_doc.get("custom_mpesa_reconciled_payments"):
					from klik_pos.api.mpesa import _finalize_mpesa_reconciliation

					mpesa_reconciliation = _finalize_mpesa_reconciliation(invoice_doc, mpesa_allocation)
			except Exception as e:
				# A race already lost the whole transaction, savepoints included.
				_raise_lock_race(e)
				_rollback_to_savepoint("klik_submit_draft")
				raise

			if held_order_id:
				from klik_pos.api.sales_order import _remove_checked_out_order

				_remove_checked_out_order(held_order_id)

			response = {
				"success": True,
				"message": f"Draft invoice {invoice_id} submitted successfully",
				"invoice_name": invoice_doc.name,
				"invoice": invoice_doc,
				"customer_credit": _apply_checkout_credit(invoice_doc, credit_rows),
			}
			if mpesa_reconciliation:
				response["mpesa_reconciliation"] = mpesa_reconciliation
			return response

	except frappe.DoesNotExistError:
		if draft_touched:
			_rollback_to_savepoint("klik_submit_draft_invoice")
		return {"success": False, "error": f"Invoice {invoice_id} not found"}
	except Exception as e:
		_raise_lock_race(e)
		if draft_touched:
			_rollback_to_savepoint("klik_submit_draft_invoice")
		frappe.log_error(frappe.get_traceback(), f"Error submitting draft invoice {invoice_id}")
		return {"success": False, "error": str(e)}

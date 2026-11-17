"""The draft Sales Order an M-Pesa STK push is sent from, until the cashier submits the sale.

The payment dialog used to make a draft Sales Invoice before every push and keep it for good
once a push went out, so every push the customer never paid left a POS-numbered invoice draft
behind. A push now names a draft Sales Order (`custom_klik_mpesa_order`). Submitting the sale
turns it into the invoice the way a held order checks out: klik's own submit builds the invoice
from the cart, the invoice records the order (`powerpack_source_order`), every push sent from
the order is pointed at the invoice, and the order is deleted.

The order is on the Held tab from the first push (`custom_is_klik_held`), so a push that may
still pay - or already has - is never left on an order nobody can see, whatever becomes of
the checkout that sent it. Leaving checkout deletes it unless such a push exists; then it
stays held, to be finished from the Held tab.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, get_datetime, now_datetime

from klik_pos.api import sales_order
from klik_pos.api.mpesa import _klik_entry_for_transid
from klik_pos.api.sales_invoice import (
	_create_draft_invoice,
	_keeping_naming_series,
	_parse_extra_fields,
	_submit_draft_invoice,
	parse_invoice_data,
)
from klik_pos.overrides.loss_of_sale import fold_los_into_quantity

EXPRESS = "Mpesa Express Request"
REGISTER = "Mpesa C2B Payment Register"
# A push in either state may pay the order, or has: the order must not be lost.
SAVEPOINT = "klik_submit_mpesa_order"
# Safaricom drops an unanswered prompt after about a minute. A push still In Progress well
# after that lost its callback, and nothing will pay it now (frappe_mpsa_payments' own
# duplicate check uses the same lifetime).
PROMPT_LIFETIME_MINUTES = 5


def _pushes(order_name, statuses=None):
	"""Pushes sent from the order, newest first."""
	if not frappe.db.exists("DocType", EXPRESS):
		return []
	filters = {"reference_doctype": "Sales Order", "reference_name": order_name}
	if statuses:
		filters["status"] = ["in", list(statuses)]
	return frappe.get_all(EXPRESS, filters=filters, pluck="name", order_by="creation desc")


def _waiting(order_name):
	"""Pushes of the order still waiting on the customer - not ones whose callback was lost."""
	if not frappe.db.exists("DocType", EXPRESS):
		return []
	return frappe.get_all(
		EXPRESS,
		filters={
			"reference_doctype": "Sales Order",
			"reference_name": order_name,
			"status": "In Progress",
			"creation": [">", add_to_date(None, minutes=-PROMPT_LIFETIME_MINUTES)],
		},
		pluck="name",
		order_by="creation desc",
	)


def live_push(order_name):
	"""A push sent from the order that may still pay it, or already did; else None."""
	pushes = _pushes(order_name, ("Completed",)) or _waiting(order_name)
	return pushes[0] if pushes else None


def last_push(order_name):
	"""The push checkout picks up for the order, or None: a paid one, else one still
	waiting on the customer, else the newest."""
	pushes = (
		_pushes(order_name, ("Completed",)) or _pushes(order_name, ("In Progress",)) or _pushes(order_name)
	)
	if not pushes:
		return None
	return frappe.db.get_value(
		EXPRESS,
		pushes[0],
		[
			"name",
			"status",
			"amount",
			"phone_number",
			"transaction_id",
			"checkout_request_id",
			"payment_gateway",
			"result_desc",
		],
		as_dict=True,
	)


def delete_order(so):
	"""Delete a draft order nothing live depends on.

	A failed push - or one whose callback was lost - still names the order, and Frappe refuses
	to delete a document a submitted one links to. Neither will pay it, so neither stands in
	the way. A live push, or any other link, still does.
	"""
	from frappe.model.delete_doc import (
		check_if_doc_is_linked,
		get_dynamic_linked_docs,
		raise_link_exists_exception,
	)

	check_if_doc_is_linked(so)
	live = set(_pushes(so.name, ("Completed",))) | set(_waiting(so.name))
	for link in get_dynamic_linked_docs(so, "Delete"):
		if link["reference_doctype"] == EXPRESS and link["reference_docname"] not in live:
			continue
		# An active Sales Order workflow's open action; frappe's own on_trash clears it.
		if link["reference_doctype"] == "Workflow Action":
			continue
		raise_link_exists_exception(
			so, link["reference_doctype"], link["reference_docname"], link["at_position"]
		)
	with _keeping_naming_series():
		frappe.delete_doc("Sales Order", so.name, ignore_permissions=True, force=True)


def _load(order_id):
	"""Lock the M-Pesa order and check the caller may act on it."""
	sales_order._lock_held_order(order_id)
	so = frappe.get_doc("Sales Order", order_id)
	if not so.get("custom_klik_mpesa_order"):
		frappe.throw(_("Order {0} is not an M-Pesa checkout order.").format(order_id))
	if not sales_order._may_act_on_held_order(so):
		frappe.throw(_("You are not allowed to act on order {0}.").format(order_id))
	return so


@frappe.whitelist(methods=["POST"])
def save_mpesa_order(data):
	"""Create the order a push is sent from, or bring an existing one up to the cart.

	Called before every push, so the order - and the amount the push asks for - always matches
	what the cashier is charging for.
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)

		from klik_pos.api.pos_profile import validate_required_extra_fields

		validate_required_extra_fields(_parse_extra_fields(data))
		(
			customer,
			items,
			_amount_paid,
			sales_and_tax_charges,
			_mode_of_payment,
			business_type,
			roundoff_amount,
			delivery_charge,
			delivery_personnel,
			_is_credit_sale,
			_allow_partial_payment,
			_due_date,
			salesperson,
			tax_id,
			_enable_background_submission,
			_loyalty_redemption,
		) = parse_invoice_data(data)
		# A Sales Order refuses qty-0 lines: hold what was asked for, as a held order does.
		fold_los_into_quantity(items)

		cart_meta = sales_order._build_cart_meta(
			data,
			items,
			business_type,
			salesperson,
			tax_id,
			delivery_charge,
			delivery_personnel,
			sales_and_tax_charges,
			roundoff_amount,
		)
		order_discount_amount = flt(data.get("orderDiscountAmount") or 0)

		order_id = data.get("mpesa_order_id")
		if order_id:
			gone = sales_order._held_order_gone(order_id)
			if gone:
				return sales_order._gone_response(order_id, gone)
			so = _load(order_id)
			if so.docstatus != 0:
				frappe.throw(_("Order {0} is no longer a draft.").format(order_id))
			sales_order._rebuild_sales_order(
				so, customer, items, sales_and_tax_charges, cart_meta, order_discount_amount
			)
			so.custom_klik_cart_meta = json.dumps(cart_meta)
		else:
			so = sales_order._build_sales_order_doc(
				customer, items, sales_and_tax_charges, cart_meta, order_discount_amount
			)

		# On the Held tab from the first push: whatever becomes of this checkout, a push that
		# may pay the order never leaves it where nobody can see it.
		so.custom_is_klik_held = 1
		so.custom_klik_mpesa_order = 1
		if so.is_new():
			so.insert(ignore_permissions=True)
		else:
			so.save(ignore_permissions=True)

		# Sales Order.tax_id is fetched from the Customer on every save; keep the buyer's PIN.
		if tax_id and so.meta.has_field("tax_id") and so.tax_id != tax_id:
			so.db_set("tax_id", tax_id)

		return {
			"success": True,
			"order_name": so.name,
			"grand_total": flt(so.rounded_total or so.grand_total),
		}
	except Exception as e:
		# Before logging, as create_held_order does: insert() takes the order's number before
		# validating, and a swallowed failure would let the request commit that burnt number.
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "Save M-Pesa Order Error")
		return {"success": False, "message": str(e)}


def _create_invoice_draft(order_id, data):
	"""The invoice draft submit_draft_invoice finishes; it rebuilds it from the cart anyway."""
	# Inside this checkout's savepoint: the un-retried body, so a lock race unwinds the whole
	# checkout rather than a retry rolling back past it.
	result = _create_draft_invoice(
		{
			**(data or {}),
			# Same as the draft M-Pesa used to make up front: it is paid by the submit, so the
			# cash-sale "positive payment" check does not apply to the draft.
			"status": "held",
			"enable_background_invoice_submission": False,
		}
	)
	if not result.get("success"):
		frappe.throw(
			result.get("message") or _("Could not create the invoice for order {0}.").format(order_id)
		)
	return result["invoice_name"]


def _invoice_already_made(order_id):
	"""The response for a submit that already went through, or None.

	The order is gone once its invoice exists, and its pushes then name that invoice. A
	retried submit (the reply was lost on the way back) must answer with it, not ring the
	sale up a second time.
	"""
	if frappe.db.exists("Sales Order", order_id) or not frappe.db.exists("DocType", EXPRESS):
		return None
	invoice = frappe.db.get_value(
		EXPRESS,
		{"account_reference": order_id, "reference_doctype": "Sales Invoice"},
		"reference_name",
	)
	if not invoice or not frappe.db.exists("Sales Invoice", invoice):
		return None
	invoice_doc = frappe.get_doc("Sales Invoice", invoice)
	if invoice_doc.docstatus == 2:
		return None
	if not invoice_doc.has_permission("read"):
		return None
	return {
		"success": True,
		"replayed": True,
		"message": _("Order {0} was already submitted as {1}.").format(order_id, invoice),
		"invoice_name": invoice,
		"queue_status": invoice_doc.get("queue_status"),
		"invoice": invoice_doc,
	}


def _paid_push_refusal(order_id, data):
	"""Why the cart cannot be submitted against the order's paid pushes, or None.

	A paid push's money must be on the invoice in full - recorded for less (cash typed after it
	paid shrank the M-Pesa row) the till is short what the customer paid. A sale paid by two
	pushes was paid twice: that is for the desk to settle and refund, not the till.
	"""
	paid = _pushes(order_id, ("Completed",))
	if not paid:
		return None
	if len(paid) > 1:
		return (
			"mpesa_paid_twice",
			_(
				"This sale was paid more than once by M-Pesa ({0}). Finish it from the desk and "
				"refund the extra payment."
			).format(", ".join(reversed(paid))),
		)
	push = paid[0]
	used = _receipt_used(push)
	if used:
		entry = frappe.db.get_value(
			REGISTER, {"transid": used, "payment_entry": ["is", "set"]}, "payment_entry"
		) or _klik_entry_for_transid(used)
		return (
			"mpesa_receipt_used",
			_(
				"M-Pesa receipt {0} of request {1} is already booked in {2}. Finish this sale from "
				"the desk."
			).format(used, push, entry),
		)
	recorded = sum(
		flt(row.get("amount"))
		for row in (data or {}).get("paymentMethods") or []
		if isinstance(row, dict) and row.get("custom_reference_text") == push
	)
	collected = flt(frappe.db.get_value(EXPRESS, push, "amount"))
	if flt(recorded, 2) < flt(collected, 2):
		return (
			"mpesa_payment_missing",
			_(
				"M-Pesa request {0} for this sale paid {1}, but this checkout records {2} of it. "
				"Reopen the sale from Held orders so the payment is picked up in full."
			).format(push, collected, recorded),
		)
	return None


def _receipt_used(push_name):
	"""The paid push's receipt number when its register row is already used by a sale, else None.

	The row is locked by name (`transid` has no index, so a locked read by it would lock the whole
	register): a till that turned it into a Payment Entry a moment ago is seen, and one about to
	waits for this sale, then finds the row touched and refuses (mpesa._ensure_receipt_payment_entries).
	"""
	transid = frappe.db.get_value(EXPRESS, push_name, "transaction_id")
	if not transid:
		return None
	names = frappe.get_all(REGISTER, filters={"transid": transid}, pluck="name")
	rows = (
		frappe.db.sql(
			f"SELECT payment_entry FROM `tab{REGISTER}` WHERE name IN %(names)s FOR UPDATE",
			{"names": tuple(names)},
			as_dict=True,
		)
		if names
		else []
	)
	# Spent elsewhere means a Payment Entry: every path that spends a receipt (klik's receipt
	# pick, the register's own submit, the desk's quick pay) makes one. A row submitted without
	# one is this push's own - frappe_mpsa_payments consumes it so it is not offered again.
	if any(row.payment_entry for row in rows) or _klik_entry_for_transid(transid):
		return transid
	for name in names:
		frappe.db.set_value(REGISTER, name, "modified", now_datetime(), update_modified=False)
	return None


def _hand_over_to_invoice(order_id, invoice):
	"""The order is now the invoice: its pushes follow, the invoice records it, it goes."""
	for name in _pushes(order_id):
		frappe.db.set_value(
			EXPRESS,
			name,
			{"reference_doctype": "Sales Invoice", "reference_name": invoice},
			update_modified=False,
		)
	if frappe.get_meta("Sales Invoice").has_field("powerpack_source_order") and not frappe.db.get_value(
		"Sales Invoice", invoice, "powerpack_source_order"
	):
		frappe.db.set_value(
			"Sales Invoice", invoice, "powerpack_source_order", order_id, update_modified=False
		)
	with _keeping_naming_series():
		frappe.delete_doc("Sales Order", order_id, ignore_permissions=True, force=True)


@frappe.whitelist(methods=["POST"])
def submit_mpesa_order(order_id, data=None, held_order_id=None, remarks=None):
	"""Turn the M-Pesa order into the submitted (or queued) Sales Invoice.

	All of it - the invoice, the push links, the order's deletion - happens or none of it does.
	held_order_id: the held order the cart came from, when it is not this order itself.
	"""
	if isinstance(data, str):
		data = json.loads(data)

	replay = _invoice_already_made(order_id)
	if replay:
		return replay

	frappe.db.savepoint(SAVEPOINT)
	try:
		try:
			so = _load(order_id)
		except frappe.DoesNotExistError:
			# Another request finished it while this one waited for the lock.
			return _invoice_already_made(order_id) or sales_order._gone_response(
				order_id,
				_("M-Pesa order {0} no longer exists - it was finished or cleared elsewhere.").format(
					order_id
				),
			)
		if so.docstatus != 0:
			return sales_order._gone_response(order_id, sales_order._held_order_gone(order_id))

		waiting = _waiting(order_id)
		if waiting:
			# Its payment would land on a sale already finished with other money.
			return {
				"success": False,
				"code": "mpesa_request_waiting",
				"order_id": order_id,
				"error": _("M-Pesa request {0} for this sale is still waiting on the customer.").format(
					waiting[0]
				),
			}

		refusal = _paid_push_refusal(order_id, data)
		if refusal:
			code, error = refusal
			return {"success": False, "code": code, "order_id": order_id, "error": error}

		merged = sales_order._claim_merged_orders(data, order_id, held_order_id)
		draft = _create_invoice_draft(order_id, data)
		other_held_order = held_order_id if held_order_id and held_order_id != order_id else None
		result = _submit_draft_invoice(draft, data, other_held_order, remarks)
		if not result.get("success"):
			frappe.db.rollback(save_point=SAVEPOINT)
			return result

		for done in merged:
			sales_order._remove_checked_out_order(done)
		_hand_over_to_invoice(order_id, result.get("invoice_name") or draft)
		return result
	except Exception as e:
		# All of it, not just to the savepoint: a queued submit registered its background job
		# to run after commit, and only a full rollback drops it - a savepoint keeps it, and it
		# would run for an invoice that no longer exists, or for whatever reuses its number.
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), f"Submit M-Pesa Order Error: {order_id}")
		return {"success": False, "error": str(e)}


@frappe.whitelist(methods=["POST"])
def discard_mpesa_order(order_id):
	"""The cashier left checkout without finishing the M-Pesa sale.

	The order is deleted unless a push sent from it may still pay it, or already has; then it
	is kept as a held order so the sale can be finished from the Held tab.
	"""
	state = frappe.db.get_value(
		"Sales Order",
		order_id,
		["docstatus", "custom_klik_mpesa_order"],
		as_dict=True,
		for_update=True,
	)
	if not state:
		return {"success": True, "kept": False, "message": _("Order {0} is already gone.").format(order_id)}
	so = frappe.get_doc("Sales Order", order_id)
	if (
		state.docstatus != 0
		or not state.custom_klik_mpesa_order
		or not sales_order._may_act_on_held_order(so)
	):
		# Left as it is - on the Held tab - for someone who may finish it.
		return {
			"success": False,
			"error": _("Order {0} was kept on the Held tab: it is not this checkout's to discard.").format(
				order_id
			),
		}

	live = live_push(order_id)
	if live:
		frappe.db.set_value(
			"Sales Order",
			order_id,
			{
				"custom_is_klik_held": 1,
				"custom_pos_opening_entry": sales_order.get_current_pos_opening_entry() or "",
			},
		)
		return {
			"success": True,
			"kept": True,
			"order_name": order_id,
			"message": _(
				"Order {0} was kept: M-Pesa request {1} was sent from it, and its payment needs this "
				"order. Finish it from Held orders before charging this customer again."
			).format(order_id, live),
		}

	delete_order(so)
	return {"success": True, "kept": False, "message": _("Order {0} discarded.").format(order_id)}


#: Times one cashier may ask Safaricom about pushes in a minute.
CHECKS_PER_MINUTE = 10
#: What Safaricom's status query says while the customer is still on the prompt: the "being
#: processed" error, and the "still under processing" result.
STILL_PROCESSING = ("500.001.1001", "4999")
#: At most one pull per shortcode in this many seconds, however many tills ask.
PULL_EVERY_SECONDS = 30
#: A pull for a push reaches back this far before the push was sent.
PULL_LEAD_MINUTES = 5
PULL_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
PUSH_FIELDS = [
	"name",
	"status",
	"transaction_id",
	"result_desc",
	"reference_doctype",
	"reference_name",
	"account_reference",
	"phone_number",
	"amount",
	"settings",
	"creation",
]


def _push_for_caller(request_name):
	"""The push, when it was sent from an M-Pesa order the caller may act on."""
	push = frappe.db.get_value(EXPRESS, request_name, PUSH_FIELDS, as_dict=True)
	if not push or push.reference_doctype != "Sales Order":
		frappe.throw(_("M-Pesa request {0} is not for a checkout order.").format(request_name))
	so = frappe.get_doc("Sales Order", push.reference_name)
	if not so.get("custom_klik_mpesa_order") or not sales_order._may_act_on_held_order(so):
		frappe.throw(_("You are not allowed to act on order {0}.").format(so.name))
	return push


def _request_pull(push):
	"""Ask Safaricom for the shortcode's payments from a little before the push until now, so a
	paid push's receipt reaches the register: a background job, at most once per
	PULL_EVERY_SECONDS per shortcode, whoever asks."""
	from frappe_mpsa_payments.frappe_mpsa_payments.api.m_pesa_api import pull_transactions

	key = frappe.cache.make_key(f"klik_mpesa_pull:{push.settings}")
	if not frappe.cache.set(key, 1, ex=PULL_EVERY_SECONDS, nx=True):
		return
	start = add_to_date(get_datetime(push.creation), minutes=-PULL_LEAD_MINUTES)
	pull_transactions(
		push.settings, start.strftime(PULL_TIME_FORMAT), now_datetime().strftime(PULL_TIME_FORMAT)
	)


@frappe.whitelist(methods=["POST"])
def check_mpesa_push(request_name):
	"""Ask Safaricom what became of a push whose confirmation has not arrived.

	`paid`: the customer paid. Safaricom's answer carries no receipt number: the sale submits on
	it, and a pull is requested so the receipt reaches the register, where frappe_mpsa_payments
	gives it to the push by its order number.
	`not_paid` (with Safaricom's reason): the push failed. `waiting`: the customer is still on
	the prompt. `no_answer`: Safaricom answered with an error, or not at all. Only an answer
	changes the push (frappe_mpsa_payments' status handlers); a push already answered is
	reported without asking again.
	"""
	push = _push_for_caller(request_name)
	key = frappe.cache.make_key(f"klik_mpesa_checks:{frappe.session.user}")
	pipe = frappe.cache.pipeline()
	pipe.set(key, 0, ex=60, nx=True)
	pipe.incr(key)
	if pipe.execute()[1] > CHECKS_PER_MINUTE:
		frappe.throw(
			_("Too many M-Pesa checks - wait a minute, then check again."), frappe.RateLimitExceededError
		)

	reply = None
	if push.status == "In Progress":
		from frappe_mpsa_payments.frappe_mpsa_payments.api.m_pesa_api import check_transaction_status

		reply = check_transaction_status(push.name)
		push.update(
			frappe.db.get_value(EXPRESS, push.name, ["status", "transaction_id", "result_desc"], as_dict=True)
		)

	if push.status == "Completed":
		if not push.transaction_id:
			_request_pull(push)
		return {"outcome": "paid", "transaction_id": push.transaction_id}
	if push.status == "Failed":
		return {"outcome": "not_paid", "reason": push.result_desc}
	reply = reply if isinstance(reply, dict) else {}
	code = str(reply.get("errorCode") or reply.get("ResultCode") or "")
	return {"outcome": "waiting" if code in STILL_PROCESSING else "no_answer"}

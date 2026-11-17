import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate


from klik_pos.api.sales_invoice import (
    _apply_extra_fields,
    _apply_pos_tax_treatment,
    _apply_remarks,
    _apply_walkin_party_fields,
    _get_active_pos_profile,
    _keeping_naming_series,
    _parse_extra_fields,
    _parse_remarks,
    _resolve_item_tax_details_for_line,
    _set_customer,
    get_current_pos_opening_entry,
    parse_invoice_data,
    resolve_selling_price_list,
    set_line_price_list,
)
from klik_pos.overrides.loss_of_sale import LOS_FIELD, PROFILE_FLAG as LOS_PROFILE_FLAG, split_held_items

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _assert_held_order_access(so):
    """Ensure the SO is a KLiK held order the current user may act on.

    The rule is _may_act_on_held_order, the same one get_held_orders lists by, so a cashier
    is never shown an order they are then refused when they tap it.
    """
    if not so.custom_is_klik_held:
        frappe.throw(_("Order {0} is not a KLiK held order.").format(so.name))

    if not _may_act_on_held_order(so):
        frappe.throw(_("You are not allowed to access held order {0}.").format(so.name))


def _active_till():
    """The POS Profile the caller is standing at, or None when none can be resolved."""
    try:
        pos_doc = _get_active_pos_profile()
    except Exception:
        return None
    return pos_doc if getattr(pos_doc, "name", None) else None


def _may_act_on_held_order(so):
    """Whether the caller may see, open, check out or delete this held order.

    The order must be on a till selling from the same warehouse as the caller's - finishing it
    here sells from the same stock - or carry no till at all, which nothing would otherwise
    ever reach; and be their own, unless their till lets its users act on each other's work. With no till resolvable, only their own. Managers included: one who
    needs everyone's orders is given a till that allows it, as for invoices.

    Which shift held the order is deliberately not part of it. Access used to demand the
    caller's current shift while the Held tab listed by owner alone, so an order another
    cashier held on a shift still open was listed and then refused (SO-00037 in production),
    and so was the caller's own order from another till. Handing an order from one cashier to
    another is what custom_allow_viewing_other_cashiers is for.
    """
    from klik_pos.api.sales_invoice import _profile_allows_other_cashiers, _tills_sharing_warehouse

    mine = so.owner == frappe.session.user
    till = _active_till()
    if not till:
        return mine
    if so.custom_pos_profile and so.custom_pos_profile not in _tills_sharing_warehouse(till):
        return False
    return mine or _profile_allows_other_cashiers(till)


def _held_order_gone(order_id):
    """Why a held order this POS is finishing can no longer be used, or None when it can.

    Checked out by another cashier, cleared by a shift close, or submitted from the desk.
    """
    state = frappe.db.get_value(
        "Sales Order", order_id, ["docstatus", "custom_is_klik_held"], as_dict=True
    )
    if not state:
        return _("Held order {0} no longer exists - it was checked out or cleared elsewhere.").format(order_id)
    if state.docstatus != 0:
        return _("Held order {0} is no longer a draft - it was completed or cancelled elsewhere.").format(order_id)
    return None


def _gone_response(order_id, message):
    # The code lets the POS drop its link to the order instead of retrying it forever.
    return {"success": False, "code": "held_order_gone", "order_id": order_id, "message": message}


def _claim_held_order(order_id):
    """Lock the held order a checkout is about to invoice and check the caller may.

    Returns a held_order_gone response when it is gone, else None; raises when the caller
    may not act on it. Shared by checkout_held_order and the M-Pesa draft's submit, so a
    held order is finished the same way whichever route the payment took.
    """
    gone = _held_order_gone(order_id)
    if gone:
        return _gone_response(order_id, gone)

    try:
        _lock_held_order(order_id)
    except frappe.DoesNotExistError as e:
        # Another cashier's checkout deleted it while this one waited for the lock.
        return _gone_response(order_id, str(e))
    so = frappe.get_doc("Sales Order", order_id)
    _assert_held_order_access(so)
    if so.docstatus != 0:
        frappe.throw(_("Sales Order {0} is not a draft.").format(order_id))
    if so.get("custom_klik_mpesa_order"):
        from klik_pos.api.mpesa_order import live_push

        live = live_push(order_id)
        if live:
            # Finished any other way, the push's payment would land on nothing - or be paid
            # for again. The M-Pesa checkout picks it up when the order is reopened.
            frappe.throw(
                _(
                    "Order {0} has M-Pesa request {1}, which may still pay it or already has. "
                    "Finish it by M-Pesa: reopen it and submit the confirmed payment."
                ).format(order_id, live)
            )
    return None


def _hold_mpesa_order(so):
    """Holding the sale an M-Pesa order stands for makes it an ordinary held order - but not
    while a push sent from it may still pay it, or already has."""
    from klik_pos.api.mpesa_order import live_push

    live = live_push(so.name)
    if live:
        frappe.throw(
            _(
                "Order {0} can't be held: M-Pesa request {1} sent from it may still pay it, "
                "or already has. Finish the sale instead."
            ).format(so.name, live)
        )
    so.custom_klik_mpesa_order = 0
    so.custom_is_klik_held = 1


MERGED_ORDERS_KEY = "merged_held_order_ids"


def _claim_merged_orders(data, *finishing):
    """Lock the held orders whose lines were merged into this sale, before any of it is done.

    The caller deletes them once the sale (or re-hold) succeeds. One that is gone, at another
    till, or waiting on an M-Pesa push refuses the whole sale: its lines may already be sold.
    Plain errors, never held_order_gone - that code makes the POS drop the cart's own order.
    """
    ids = (data or {}).get(MERGED_ORDERS_KEY) if isinstance(data, dict) else None
    ids = [i for i in dict.fromkeys(ids or []) if i and i not in finishing]
    for order_id in ids:
        why = _held_order_gone(order_id)
        if not why:
            try:
                _lock_held_order(order_id)
            except frappe.DoesNotExistError as e:
                why = str(e)
        if why:
            frappe.throw(_("{0} Its lines were merged into this sale: remove them and try again.").format(why))
        so = frappe.get_doc("Sales Order", order_id)
        _assert_held_order_access(so)
        if so.get("custom_klik_mpesa_order"):
            frappe.throw(_("Order {0} is an M-Pesa order and can't be merged into another sale.").format(order_id))
    return ids


def _remove_checked_out_order(order_id):
    """The order is now an invoice: take it off the Held tab."""
    try:
        with _keeping_naming_series():
            frappe.delete_doc("Sales Order", order_id, ignore_permissions=True)
    except Exception as del_err:
        frappe.logger().warning(
            "Could not delete held SO %s after checkout: %s", order_id, del_err
        )


# ---------------------------------------------------------------------------
# Price approval (optional: cecypo_powerpack). Everything here is a no-op when the
# fields or the Sales Order workflow are absent.
# ---------------------------------------------------------------------------

APPROVAL_STATE_FIELD = "workflow_state"
PRICE_BREACH_FIELD = "powerpack_price_breach"
REQUEST_APPROVAL_ACTION = "Request Price Approval"


def _held_order_workflow():
    from frappe.model.workflow import get_workflow_name

    name = get_workflow_name("Sales Order")
    return frappe.get_cached_doc("Workflow", name) if name else None


def _approval_fields(so):
    """The held order's approval state and breach flag, or null / 0 without PowerPack."""
    meta = frappe.get_meta("Sales Order")
    state = so.get(APPROVAL_STATE_FIELD) if meta.has_field(APPROVAL_STATE_FIELD) else None
    breach = cint(so.get(PRICE_BREACH_FIELD)) if meta.has_field(PRICE_BREACH_FIELD) else 0
    return {"approval_state": state or None, "price_breach": breach}


def _held_order_transitions(so, workflow):
    """The caller's draft-to-draft transitions from the order's current state."""
    from frappe.model.workflow import is_transition_condition_satisfied

    state = so.get(workflow.workflow_state_field) or workflow.states[0].state
    draft_states = {s.state for s in workflow.states if cint(s.doc_status) == 0}
    roles = set(frappe.get_roles())
    return [
        t
        for t in workflow.transitions
        if t.state == state
        and t.allowed in roles
        and t.next_state in draft_states
        and is_transition_condition_satisfied(t, so)
    ]


def _available_held_order_actions(so):
    workflow = _held_order_workflow()
    if not workflow:
        return []
    actions = []
    for t in _held_order_transitions(so, workflow):
        if t.action not in actions:
            actions.append(t.action)
    return actions


APPROVER_ACTIONS = ("Approve", "Reject")


def _my_approval_actions(order_name):
    """Approve / Reject, where the caller may take them on this order now: their role is
    allowed the transition and it is not their own request (unless the workflow permits self
    approval). Empty for anyone else, so the till shows the buttons only to an approver."""
    from frappe.model.workflow import has_approval_access

    workflow = _held_order_workflow()
    if not workflow:
        return []
    so = frappe.get_doc("Sales Order", order_name)
    return [
        t.action
        for t in _held_order_transitions(so, workflow)
        if t.action in APPROVER_ACTIONS and has_approval_access(frappe.session.user, so, t)
    ]


def _apply_held_order_action(so, action):
    """frappe.model.workflow.apply_workflow for a held order, after klik's own access check.

    Held orders are saved with ignore_permissions (a cashier need not hold Sales Order
    write), so frappe's apply_workflow, which saves with the caller's permissions, cannot
    be used. Only draft-to-draft transitions: the POS never submits an order this way.
    """
    from frappe.model.workflow import has_approval_access

    workflow = _held_order_workflow()
    if not workflow:
        frappe.throw(_("Sales Order has no active workflow."))
    transition = next((t for t in _held_order_transitions(so, workflow) if t.action == action), None)
    if not transition:
        frappe.throw(_("{0} is not available for held order {1}.").format(action, so.name))
    if not has_approval_access(frappe.session.user, so, transition):
        frappe.throw(_("Self approval is not allowed"))
    so.set(workflow.workflow_state_field, transition.next_state)
    so.flags.ignore_permissions = True
    so.save()
    so.add_comment("Workflow", _(transition.next_state))
    return transition.next_state


def _request_approval_if_needed(so):
    """Send a breaching held order for price approval. True when a request was made."""
    if not cint(so.get(PRICE_BREACH_FIELD)):
        return False
    if REQUEST_APPROVAL_ACTION not in _available_held_order_actions(so):
        return False
    _apply_held_order_action(so, REQUEST_APPROVAL_ACTION)
    return True


def _lock_held_order(order_id):
    """Hold the order's row until this request ends, so only one checkout can invoice it.

    Two cashiers may open the same order. Checkout commits nothing before it deletes the
    order, so the second one waits on this lock and then finds the order gone, rather than
    both reading it and each raising an invoice.
    """
    if not frappe.db.get_value("Sales Order", order_id, "name", for_update=True):
        raise frappe.DoesNotExistError(
            _("Held order {0} has already been checked out or deleted.").format(order_id)
        )


def _build_cart_meta(data, parsed_items, business_type, salesperson, tax_id,
                     delivery_charge, delivery_personnel, sales_and_tax_charges, roundoff_amount):
    """Serialise all POS-specific cart state that doesn't live on the SO itself."""
    raw_discounts = data.get("itemDiscounts", {}) if isinstance(data, dict) else {}
    item_discounts = {}
    item_lines = []

    for item in parsed_items:
        item_code = item["id"]
        # The cart's line id (two lines of one item differ); older carts only sent the code.
        discount_data = raw_discounts.get(item.get("line_id")) or raw_discounts.get(item_code) or {}
        if isinstance(discount_data, str):
            try:
                discount_data = json.loads(discount_data)
            except Exception:
                discount_data = {}

        # By code too, for older readers; a second line of the code overwrites it (last wins).
        entry = item_discounts[item_code] = {
            "discountPercentage": flt(item.get("discountPercentage") or 0),
            "discountAmount": flt(item.get("discountAmount") or 0),
            "customRate": discount_data.get("customRate"),
            "customRateIncludesTax": bool(discount_data.get("customRateIncludesTax")),
            "bundle_entries": item.get("bundle_entries") or [],
            "item_tax_template": item.get("item_tax_template") or "",
            "item_tax_rate": item.get("item_tax_rate") or {},
            "description": item.get("description") or "",
            "selectedPriceList": discount_data.get("selectedPriceList") or "",
        }
        item_lines.append({**entry, "item_code": item_code})

    # Persist the full frontend customer object (if sent) so resume can restore it
    # directly, without a re-fetch + re-transform round-trip.
    customer_data = data.get("customerData") if isinstance(data, dict) else None
    if isinstance(customer_data, str):
        try:
            customer_data = json.loads(customer_data)
        except Exception:
            customer_data = None

    return {
        "itemDiscounts": item_discounts,
        # One entry per line, in order: two lines of one item keep their own discounts.
        "itemLines": item_lines,
        "appliedCoupons": data.get("appliedCoupons") or [] if isinstance(data, dict) else [],
        "businessType": business_type or "B2C",
        "salesperson": salesperson,
        "tax_id": tax_id,
        "deliveryCharge": flt(delivery_charge),
        "deliveryPersonnel": delivery_personnel,
        "SalesTaxCharges": sales_and_tax_charges,
        "roundOffAmount": flt(roundoff_amount),
        "customer": customer_data,
        # The list the cart was on; the held order records it (if the cashier may use it).
        "selling_price_list": (data.get("selling_price_list") or None) if isinstance(data, dict) else None,
        "walkin_name": data.get("walkin_name") if isinstance(data, dict) else None,
        "walkin_phone": data.get("walkin_phone") if isinstance(data, dict) else None,
        "extra_fields": _parse_extra_fields(data),
        "shipping_rule": (data.get("shipping_rule") or None) if isinstance(data, dict) else None,
        # None when the cart sent no remarks at all - an older POS - so a re-hold keeps them.
        "remarks": (_parse_remarks(data) or "") if isinstance(data, dict) and "remarks" in data else None,
    }


def line_meta(cart_meta, idx, item_code):
    """The saved per-line cart state of document row `idx` (rows keep the cart's line order),
    when that line is for this item; otherwise, and for older documents, by item code."""
    lines = cart_meta.get("itemLines")
    if isinstance(lines, list) and idx < len(lines) and isinstance(lines[idx], dict):
        line = lines[idx]
        # A row deleted or moved in desk shifts the rest: never take another item's line.
        if line.get("item_code") in (None, item_code):
            return line
    return (cart_meta.get("itemDiscounts") or {}).get(item_code) or {}


def _apply_order_discount(so, pos_profile, order_discount_amount):
    """Set the Sales Order's native additional-discount fields, gated by the
    POS Profile's Allow Discount Change permission (same flag used for the
    Sales Invoice's order-level discount)."""
    order_discount_amount = flt(order_discount_amount or 0)
    if order_discount_amount < 0:
        frappe.throw(_("Discount amount cannot be negative."))
    if order_discount_amount > 0:
        if not cint(getattr(pos_profile, "allow_discount_change", 0) or 0):
            frappe.throw(_("Discount changes are not allowed for this POS Profile."))
        so.apply_discount_on = "Grand Total"
        so.discount_amount = order_discount_amount
    else:
        so.apply_discount_on = ""
        so.discount_amount = 0


def _so_item_row(so, pos_profile, item, warehouse):
    """A held order's item row, including the per-item tax template/rate the Sales Invoice
    built from the same cart would resolve.

    Deliberately does NOT pin price_list_rate from the cart the way
    build_sales_invoice_doc._prepare_item_data does for an invoice - see
    _reassert_pos_line_prices' restore_price_list_rate for why: klik's own heldOrderToCart
    round-trips the list price back into the client payload on recall, and pinning it from
    that let it drift with whatever a Pricing Rule margin had done to it on the previous
    cycle. A held order lets set_missing_values fetch the Item Price fresh every time
    instead, which is what test_held_order_pricing_rule.py pins down.

    Without item_tax_template/item_tax_rate, _apply_pos_tax_treatment's _populate_per_item_taxes
    has nothing to build a tax row from, and the held order would still price VAT-bearing
    items as though they carried no item tax at all.
    """
    # A Loss of Sale till holds the cart's split, so the order (and its print) is for what is
    # in stock; elsewhere the order holds the full ask.
    asked = flt(item.get("quantity")) + flt(item.get("los_qty"))
    los_qty = flt(item.get("los_qty")) if cint(pos_profile.get(LOS_PROFILE_FLAG)) else 0
    row = {
        "item_code": item["id"],
        "qty": asked - los_qty if asked else 1,
        LOS_FIELD: los_qty,
        "rate": flt(item.get("price") or 0),
        "uom": item.get("uom") or "",
        "delivery_date": nowdate(),
        "warehouse": warehouse,
    }
    # The cashier's own wording; set_missing_values fills the Item's otherwise.
    if item.get("description"):
        row["description"] = item["description"]
    set_line_price_list(row, "Sales Order Item", item.get("price_list"))
    item_tax_template, item_tax_rate = _resolve_item_tax_details_for_line(so, item, pos_profile)
    if item_tax_template:
        row["item_tax_template"] = item_tax_template
    if item_tax_rate:
        row["item_tax_rate"] = frappe.as_json(item_tax_rate)
    return row


def _build_sales_order_doc(customer, items, sales_and_tax_charges, cart_meta, order_discount_amount=0.0):
    """Create a new draft Sales Order from parsed cart data."""
    pos_profile = _get_active_pos_profile()
    opening_entry = get_current_pos_opening_entry() or ""
    warehouse = getattr(pos_profile, "warehouse", "") or ""

    so = frappe.new_doc("Sales Order")
    so.customer = customer
    so.order_type = "Sales"
    so.transaction_date = nowdate()
    so.delivery_date = nowdate()
    so.company = pos_profile.company
    so.currency = pos_profile.currency
    # A held order is priced from the till, as checkout re-prices it; only the User Permission check applies.
    so.selling_price_list = resolve_selling_price_list(cart_meta.get("selling_price_list"), None, pos_profile)
    so.custom_pos_profile = pos_profile.name
    so.custom_pos_opening_entry = opening_entry
    so.custom_is_klik_held = 1
    so.custom_klik_cart_meta = json.dumps(cart_meta)
    # The cart already applied Pricing Rules through get_cart_pricing. Left on, ERPNext
    # applies them again here and calculate_item_rate replaces the cashier's rate: a 5%
    # margin rule held a keyed 2,000 as 2,142, and recalling the order showed a discount
    # nobody gave. The Sales Invoice path does the same in build_sales_invoice_doc.
    so.ignore_pricing_rule = 1

    _apply_order_discount(so, pos_profile, order_discount_amount)

    if cart_meta.get("tax_id") and so.meta.has_field("tax_id"):
        so.tax_id = cart_meta.get("tax_id")
    _apply_walkin_party_fields(
        so,
        walkin_name=cart_meta.get("walkin_name"),
        walkin_phone=cart_meta.get("walkin_phone"),
        walkin_tax_id=cart_meta.get("tax_id"),
    )
    _apply_extra_fields(so, cart_meta.get("extra_fields"))
    if cart_meta.get("remarks") is not None:
        _apply_remarks(so, {"remarks": cart_meta.get("remarks")})

    for item in items:
        so.append("items", _so_item_row(so, pos_profile, item, warehouse))

    # Same tax treatment as the invoice checked out from this cart - the till's
    # taxes-and-charges template, per-item tax rows, and tax-inclusive basic rate if the POS
    # Profile calls for it - so a held order's totals, and the net rate the minimum-selling-
    # price floor judges it on, match what checkout will actually produce. A held order used
    # to only set taxes_and_charges and skip all of this, pricing every rate as tax-exclusive.
    pos_line_prices = [
        (row.item_code, flt(row.rate), flt(row.price_list_rate)) for row in so.items
    ]
    _apply_pos_tax_treatment(so, pos_profile, sales_and_tax_charges, pos_line_prices, restore_price_list_rate=False)
    return so


def _rebuild_sales_order(so, customer, items, sales_and_tax_charges, cart_meta, order_discount_amount=0.0):
    """Overwrite an existing held Sales Order with refreshed cart data."""
    pos_profile = _get_active_pos_profile()
    warehouse = getattr(pos_profile, "warehouse", "") or ""

    _set_customer(so, customer)
    # Held again today, so dated today: the order is the cart as it stands now.
    so.transaction_date = nowdate()
    so.delivery_date = nowdate()
    # Whoever holds it now owns where it lives. A cashier may take over an order held on
    # another shift; left stamped with that shift, its close would delete the order from
    # under them.
    so.custom_pos_profile = pos_profile.name
    so.custom_pos_opening_entry = get_current_pos_opening_entry() or ""
    so.selling_price_list = resolve_selling_price_list(cart_meta.get("selling_price_list"), None, pos_profile)
    # Orders held before this flag existed carry 0; see _build_sales_order_doc.
    so.ignore_pricing_rule = 1
    so.set("items", [])
    # _apply_pos_tax_treatment appends to "taxes"; without clearing it here, re-holding an
    # already-held order would duplicate every tax row instead of replacing them.
    so.set("taxes", [])

    _apply_order_discount(so, pos_profile, order_discount_amount)

    if cart_meta.get("tax_id") and so.meta.has_field("tax_id"):
        so.tax_id = cart_meta.get("tax_id")
    _apply_walkin_party_fields(
        so,
        walkin_name=cart_meta.get("walkin_name"),
        walkin_phone=cart_meta.get("walkin_phone"),
        walkin_tax_id=cart_meta.get("tax_id"),
    )
    _apply_extra_fields(so, cart_meta.get("extra_fields"))
    if cart_meta.get("remarks") is not None:
        _apply_remarks(so, {"remarks": cart_meta.get("remarks")})

    for item in items:
        so.append("items", _so_item_row(so, pos_profile, item, warehouse))

    pos_line_prices = [
        (row.item_code, flt(row.rate), flt(row.price_list_rate)) for row in so.items
    ]
    _apply_pos_tax_treatment(so, pos_profile, sales_and_tax_charges, pos_line_prices, restore_price_list_rate=False)


# ---------------------------------------------------------------------------
# Whitelisted API endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_held_order(data):
    """Create or update a held Sales Order from the POS cart."""
    try:
        if isinstance(data, str):
            data = json.loads(data)

        from klik_pos.api.pos_profile import validate_required_extra_fields
        validate_required_extra_fields(_parse_extra_fields(data))

        target_order_id = data.get("held_order_id") if isinstance(data, dict) else None
        # 'Ask manager' at a till with the price-list floor: PowerPack judges the order on the
        # list now and the workflow sends it for approval. A plain Hold is judged at checkout.
        ask_manager = bool(isinstance(data, dict) and cint(data.get("request_price_approval")))

        # Every field is named, even the unused ones. Collapsing them to `_` rebound the
        # translation function imported at the top of this module, so the frappe.throw below
        # raised "'NoneType' object is not callable" instead of its own message - and the
        # except block reported that to the cashier as the reason their order would not hold.
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

        cart_meta = _build_cart_meta(
            data, items, business_type, salesperson, tax_id,
            delivery_charge, delivery_personnel, sales_and_tax_charges, roundoff_amount,
        )
        order_discount_amount = flt(data.get("orderDiscountAmount") or 0) if isinstance(data, dict) else 0

        if target_order_id:
            gone = _held_order_gone(target_order_id)
            if gone:
                return _gone_response(target_order_id, gone)
            so = frappe.get_doc("Sales Order", target_order_id)
            if so.get("custom_klik_mpesa_order"):
                _hold_mpesa_order(so)
            # The id comes from the client and the save below ignores permissions, so without
            # this any Sales Order - held or not, any till - could be rewritten by id.
            _assert_held_order_access(so)
            if so.docstatus != 0:
                frappe.throw(_("Cannot update held order {0}: it is no longer a draft.").format(target_order_id))
            merged = _claim_merged_orders(data, target_order_id)
            _rebuild_sales_order(so, customer, items, sales_and_tax_charges, cart_meta, order_discount_amount)
            so.custom_klik_cart_meta = json.dumps(cart_meta)
            so.flags.powerpack_request_list_approval = ask_manager
            so.save(ignore_permissions=True)
        else:
            merged = _claim_merged_orders(data)
            so = _build_sales_order_doc(customer, items, sales_and_tax_charges, cart_meta, order_discount_amount)
            so.flags.powerpack_request_list_approval = ask_manager
            so.insert(ignore_permissions=True)

        for order_id in merged:
            _remove_checked_out_order(order_id)

        # Sales Order.tax_id is fetched from Customer.tax_id on every save, which wipes the
        # walk-in buyer's PIN the cashier typed. Put it back, as the invoice path does.
        if tax_id and so.meta.has_field("tax_id") and so.tax_id != tax_id:
            so.db_set("tax_id", tax_id)

        approval_requested = _request_approval_if_needed(so)
        return {
            "success": True,
            "order_name": so.name,
            "approval_requested": approval_requested,
            **_approval_fields(so),
        }

    except Exception as e:
        # Roll back BEFORE logging, and never return without rolling back.
        # so.insert() assigns the Sales Order name - incrementing the Document Naming Rule
        # counter - before it validates, so swallowing the exception without a rollback
        # lets Frappe's request handler commit that increment while no order exists. The
        # order number is then burned permanently. This is the same defect that cost
        # production 38 POS receipt numbers in Sept 2026; see the "naming-counter burn
        # guard" comment in api/sales_invoice.py for the full mechanism.
        # The Error Log row must be written after the rollback or it is discarded with it.
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Create Held Order Error")
        return {"success": False, "message": str(e)}


def _mpesa_fields(so):
    """An M-Pesa order resumed from the Held tab carries its push, so checkout picks it up
    instead of charging the customer again."""
    from klik_pos.api.mpesa_order import last_push

    is_mpesa = cint(so.get("custom_klik_mpesa_order"))
    return {"mpesa_order": is_mpesa, "mpesa_request": last_push(so.name) if is_mpesa else None}


@frappe.whitelist()
def get_held_order_details(order_id):
    """Return items, customer and cart metadata for resuming a held order."""
    try:
        so = frappe.get_doc("Sales Order", order_id)
        _assert_held_order_access(so)

        cart_meta = {}
        if so.custom_klik_cart_meta:
            try:
                cart_meta = json.loads(so.custom_klik_cart_meta)
            except Exception:
                pass


        # Batch-fetch item names
        item_codes = [row.item_code for row in so.items]
        item_names = {}
        if item_codes:
            rows = frappe.get_all(
                "Item",
                filters={"name": ["in", item_codes]},
                fields=["name", "item_name"],
            )
            item_names = {r.name: r.item_name for r in rows}

        # The full ask, split again against the stock at this till now. An M-Pesa order opens
        # as held: its push was for the held total.
        lines = [
            {
                "id": row.item_code,
                "quantity": flt(row.qty),
                "los_qty": flt(row.get(LOS_FIELD)),
                "uom": row.uom,
                "bundle_entries": line_meta(cart_meta, i, row.item_code).get("bundle_entries") or [],
            }
            for i, row in enumerate(so.items)
        ]
        split_held_items(lines, None if so.get("custom_klik_mpesa_order") else _active_till())

        items = []
        for i, (row, line) in enumerate(zip(so.items, lines)):
            d = line_meta(cart_meta, i, row.item_code)
            items.append({
                "item_code": row.item_code,
                "item_name": item_names.get(row.item_code) or row.item_code,
                "quantity": line["quantity"],
                "los_qty": line["los_qty"],
                "price": flt(row.rate),
                "uom": row.uom or "",
                "discountAmount": flt(d.get("discountAmount") or 0),
                "discountPercentage": flt(d.get("discountPercentage") or 0),
                "customRate": d.get("customRate"),
                "customRateIncludesTax": bool(d.get("customRateIncludesTax")),
                "bundle_entries": d.get("bundle_entries") or [],
                "item_tax_template": d.get("item_tax_template") or "",
                "item_tax_rate": d.get("item_tax_rate") or {},
                "description": d.get("description") or "",
                "selectedPriceList": d.get("selectedPriceList") or "",
            })

        return {
            "success": True,
            "name": so.name,
            "customer": so.customer,
            "customer_data": cart_meta.get("customer"),
            "walkin_name": cart_meta.get("walkin_name"),
            "walkin_phone": cart_meta.get("walkin_phone"),
            "extra_fields": cart_meta.get("extra_fields") or {},
            "shipping_rule": cart_meta.get("shipping_rule"),
            "selling_price_list": so.selling_price_list,
            "items": items,
            "cart_meta": cart_meta,
            "remarks": so.get("remarks") or "",
            "grand_total": flt(so.grand_total),
            "currency": so.currency,
            "discount_amount": flt(so.discount_amount or 0),
            "apply_discount_on": so.apply_discount_on or "",
            **_approval_fields(so),
            **_mpesa_fields(so),
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Held Order Details Error")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def get_held_order_actions(order_id):
    """Workflow actions the caller may take on a held order from the POS."""
    gone = _held_order_gone(order_id)
    if gone:
        return _gone_response(order_id, gone)
    so = frappe.get_doc("Sales Order", order_id)
    _assert_held_order_access(so)
    return {"success": True, "actions": _available_held_order_actions(so), **_approval_fields(so)}


@frappe.whitelist()
def apply_held_order_action(order_id, action):
    """Apply one of get_held_order_actions' actions."""
    try:
        gone = _held_order_gone(order_id)
        if gone:
            return _gone_response(order_id, gone)
        so = frappe.get_doc("Sales Order", order_id)
        _assert_held_order_access(so)
        _apply_held_order_action(so, action)
        return {"success": True, **_approval_fields(so)}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Held Order Workflow Action Error")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def delete_held_order(order_id):
    """Delete a held (draft) Sales Order."""
    try:
        so = frappe.get_doc("Sales Order", order_id)
        _assert_held_order_access(so)
        if so.docstatus != 0:
            return {"success": False, "error": f"Cannot delete {order_id}: not a draft."}

        from klik_pos.api.mpesa_order import delete_order, live_push

        live = live_push(order_id)
        if live:
            return {
                "success": False,
                "error": _(
                    "Held order {0} can't be deleted: M-Pesa request {1} sent from it may still pay "
                    "it, or already has. Finish the sale instead."
                ).format(order_id, live),
            }
        delete_order(so)
        return {"success": True, "message": f"Held order {order_id} deleted."}
    except frappe.DoesNotExistError:
        # Already gone is what the caller wanted; the code lets the till carry on.
        return _gone_response(order_id, f"Order {order_id} not found.")
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"Delete Held Order Error: {order_id}")
        return {"success": False, "error": str(e)}


def _till_allows_other_cashiers():
    """Whether this till lets its users see held orders they did not ring.

    Delegates to the same helper Invoice History uses for invoices, so the two surfaces
    cannot drift into disagreeing about what one POS Profile setting means. A till with no
    resolvable profile reads as "no", which is the behaviour the page had before the flag
    existed.
    """
    from klik_pos.api.sales_invoice import _profile_allows_other_cashiers

    try:
        pos_doc = _get_active_pos_profile()
    except Exception:
        return False
    return _profile_allows_other_cashiers(pos_doc)


def _attach_cashier_names(orders):
    """Add each order's owner full name, the identity the history filters compare against."""
    owners = {o.get("owner") for o in orders if o.get("owner")}
    names = {}
    if owners:
        names = {
            row.name: row.full_name
            for row in frappe.get_all(
                "User", filters={"name": ["in", list(owners)]}, fields=["name", "full_name"]
            )
        }
    for order in orders:
        order["cashier_name"] = names.get(order.get("owner")) or order.get("owner") or ""
    return orders


def _attach_in_stock_totals(orders, item_rows):
    """At a till that records Loss of Sale, what each held order would come to if checked out
    here now (in_stock_total). The order itself still holds what was asked for: checkout splits
    it by the cashier's till - its setting, its warehouse - against the stock there is then.

    Scaling the total by the value in stock is close, not exact: it is off where lines carry
    different tax templates, or the order a fixed discount or shipping charge."""
    from klik_pos.overrides.loss_of_sale import (
        PROFILE_FLAG,
        _available,
        _eligible_item_codes,
        _whole_number_uoms,
        in_stock_total,
    )

    till = _active_till()
    if not till or not cint(till.get(PROFILE_FLAG)) or not till.get("warehouse"):
        return
    rows = sorted(item_rows, key=lambda row: (row.parent, row.idx))
    if not rows:
        return
    eligible = _eligible_item_codes({row.item_code for row in rows})
    whole = _whole_number_uoms(row.uom for row in rows)
    lines = {}
    for row in rows:
        lines.setdefault(row.parent, []).append({
            "key": (row.item_code, till.warehouse),
            "requested": flt(row.qty) + flt(row.get(LOS_FIELD)),
            "held": flt(row.qty),
            "factor": flt(row.conversion_factor) or 1,
            "whole": row.uom in whole,
            "rate": flt(row.rate),
            "eligible": row.item_code in eligible,
        })
    available = _available({line["key"] for order_lines in lines.values() for line in order_lines})
    for order in orders:
        if order.name in lines:
            order["in_stock_total"] = flt(in_stock_total(order.grand_total, lines[order.name], available), 2)


@frappe.whitelist()
def get_held_orders(limit=50, start=0, search="", skip_opening_entry_filter=False):
    """List held Sales Orders.

    By default lists held orders for the current POS session (used by the
    Closing Shift page). When ``skip_opening_entry_filter`` is true (used by the
    Invoice History page), the opening-entry restriction is dropped and results follow
    _may_act_on_held_order, the rule opening one is checked against: everyone, managers
    included, sees orders on the tills selling from their till's warehouse (or carrying no
    till), their own only unless the till's POS Profile sets
    ``custom_allow_viewing_other_cashiers``.

    That flag used to be read for invoices and ignored here, so a shop that had
    deliberately opened its till up still found the Held tab showing one cashier's
    held orders and nobody else's. And this listing once ignored the till while opening
    checked it, so orders were shown that could not then be opened.

    Each row carries ``cashier_name`` - the owner's full name, which is the identity
    Invoice History filters on. Returning only the email made every held order fail the
    comparison against a cashier filter holding a full name, so a cashier restricted to
    their own work saw an empty Draft tab rather than their own held orders.
    """
    try:
        if isinstance(skip_opening_entry_filter, str):
            skip_opening_entry_filter = skip_opening_entry_filter.lower() in ("true", "1", "yes")

        limit = int(limit) if limit else 50
        start = int(start) if start else 0

        filters = {"custom_is_klik_held": 1, "docstatus": 0}
        or_filters = None
        if skip_opening_entry_filter:
            # _may_act_on_held_order, expressed as filters. Keep the two in step.
            from klik_pos.api.sales_invoice import _profile_allows_other_cashiers, _tills_sharing_warehouse

            till = _active_till()
            if not till:
                filters["owner"] = frappe.session.user
            else:
                or_filters = [
                    ["custom_pos_profile", "in", _tills_sharing_warehouse(till)],
                    ["custom_pos_profile", "is", "not set"],
                ]
                if not _profile_allows_other_cashiers(till):
                    filters["owner"] = frappe.session.user
        else:
            opening_entry = get_current_pos_opening_entry()
            if opening_entry:
                filters["custom_pos_opening_entry"] = opening_entry
            elif not _till_allows_other_cashiers():
                # No active session — only show the caller's own held orders, unless the
                # till is one that lets its users see each other's.
                filters["owner"] = frappe.session.user

        so_fields = [
            "name", "customer", "customer_name", "transaction_date",
            "grand_total", "currency", "owner", "modified",
            "custom_pos_profile", "custom_pos_opening_entry",
        ]
        if frappe.db.has_column("Sales Order", APPROVAL_STATE_FIELD):
            so_fields.append(APPROVAL_STATE_FIELD)
        if frappe.db.has_column("Sales Order", PRICE_BREACH_FIELD):
            so_fields.append(PRICE_BREACH_FIELD)
        # Held orders for walk-ins all sit under one customer; the buyer's name tells them apart.
        if frappe.db.has_column("Sales Order", "custom_walkin_customer_name"):
            so_fields.append("custom_walkin_customer_name")
        # An M-Pesa order kept for its push: the Held tab says so.
        if frappe.db.has_column("Sales Order", "custom_klik_mpesa_order"):
            so_fields.append("custom_klik_mpesa_order")

        orders = frappe.get_all(
            "Sales Order",
            filters=filters,
            or_filters=or_filters,
            fields=so_fields,
            order_by="modified desc",
            limit=limit,
            start=start,
        )

        if search and search.strip():
            term = search.strip().lower()
            orders = [
                o for o in orders
                if term in (o.name or "").lower()
                or term in (o.customer_name or "").lower()
                or term in (o.customer or "").lower()
                or term in (o.get("custom_walkin_customer_name") or "").lower()
            ]

        _attach_cashier_names(orders)

        order_names = [o.name for o in orders]
        items_map = {}
        if order_names:
            all_items = frappe.get_all(
                "Sales Order Item",
                filters={"parent": ["in", order_names]},
                fields=["parent", "idx", "item_code", "item_name", "qty", LOS_FIELD, "rate", "uom", "conversion_factor"],
            )
            for row in all_items:
                items_map.setdefault(row.parent, []).append({
                    "item_code": row.item_code,
                    "item_name": row.item_name,
                    "qty": flt(row.qty),
                    "rate": flt(row.rate),
                })

            try:
                _attach_in_stock_totals(orders, all_items)
            except Exception:
                # A figure for display: the Held tab lists the orders without it.
                frappe.log_error(frappe.get_traceback(), "Held order in-stock total")

        # Resolve cashier (owner full name) so rows render/filter like invoices.
        owner_ids = list({o.owner for o in orders if o.owner})
        cashier_map = {}
        if owner_ids:
            for row in frappe.get_all(
                "User", filters={"name": ["in", owner_ids]}, fields=["name", "full_name"]
            ):
                cashier_map[row.name] = row.full_name

        for order in orders:
            order["status"] = "Held"
            order["items"] = items_map.get(order.name, [])
            order["cashier"] = cashier_map.get(order.owner) or order.owner
            order["approval_state"] = order.pop(APPROVAL_STATE_FIELD, None) or None
            order["price_breach"] = cint(order.pop(PRICE_BREACH_FIELD, 0))
            order["approval_actions"] = (
                _my_approval_actions(order.name) if order["approval_state"] == "Price Approval Pending" else []
            )
            order["mpesa_order"] = cint(order.pop("custom_klik_mpesa_order", 0))

        return {"success": True, "data": orders, "total_count": len(orders)}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Held Orders Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def checkout_held_order(order_id, data=None):
    """
    Convert a held Sales Order into a submitted Sales Invoice.
    Accepts the same checkout payload as create_and_submit_invoice.
    Deletes the Sales Order on success.
    """
    from klik_pos.api.sales_invoice import _retry_lock_race

    # Retried whole: the claim on the held order is part of what a lost race rolls back.
    return _retry_lock_race(_checkout_held_order, order_id, data)


def _checkout_held_order(order_id, data=None):
    try:
        if data and isinstance(data, str):
            data = json.loads(data)

        # Answer a replay before touching the Sales Order: a successful first call already
        # deleted it, so loading it here would fail the retry instead of returning the
        # invoice that first call created.
        from klik_pos.api.sales_invoice import (
            _checkout_request_response,
            _get_checkout_request,
            _normalize_checkout_request_id,
        )

        checkout_request_id = _normalize_checkout_request_id(
            (data or {}).get("checkout_request_id")
        )
        existing_checkout = _get_checkout_request(checkout_request_id)
        if existing_checkout:
            return _checkout_request_response(existing_checkout)

        # Only now, with a replay of this checkout ruled out, is a missing order news.
        gone = _claim_held_order(order_id)
        if gone:
            return gone
        merged = _claim_merged_orders(data, order_id)

        # A POS that sends no remarks still gets the held order's onto the invoice.
        held_remarks = frappe.db.get_value("Sales Order", order_id, "remarks") if frappe.get_meta(
            "Sales Order"
        ).has_field("remarks") else None
        if held_remarks and isinstance(data, dict) and "remarks" not in data:
            data = {**data, "remarks": held_remarks}

        # Reuse the full invoice submission pipeline
        from klik_pos.api.sales_invoice import _queue_sales_invoice
        result = _queue_sales_invoice(data, source_order=order_id)

        if result.get("success"):
            for done in (order_id, *merged):
                _remove_checked_out_order(done)

        return result

    except Exception as e:
        from klik_pos.api.sales_invoice import _raise_lock_race

        _raise_lock_race(e)
        frappe.log_error(frappe.get_traceback(), f"Checkout Held Order Error: {order_id}")
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Internal — called from pos_entry.py on shift close
# ---------------------------------------------------------------------------

def _orphaned_held_orders(opening_entry_name):
    """Held orders on this till that no shift close will ever reach.

    An order held while no shift was open is stamped with an empty opening entry, because
    that is what get_current_pos_opening_entry returns then. The sweep below matches on the
    opening entry, so those orders were never swept by anything: they sat in the Draft tab
    for good, and a cashier in a later shift could not even open them.

    Also collected: orders pointing at an opening entry that has since been closed or
    deleted, which are stranded for the same reason.

    Two guards keep this from reaching into work that is still live. The order must be on
    the same till as the shift being closed, and it must predate that shift - anything held
    since this shift opened would have been stamped with it, so an unstamped newer order
    belongs to a session this close knows nothing about.
    """
    entry = frappe.db.get_value(
        "POS Opening Entry", opening_entry_name, ["pos_profile", "period_start_date"], as_dict=True
    )
    if not entry or not entry.pos_profile or not entry.period_start_date:
        return []

    rows = frappe.db.sql(
        """
        SELECT so.name
        FROM `tabSales Order` so
        LEFT JOIN `tabPOS Opening Entry` ope ON ope.name = so.custom_pos_opening_entry
        WHERE so.custom_is_klik_held = 1
          AND so.docstatus = 0
          AND so.custom_pos_profile = %(profile)s
          AND so.modified < %(started)s
          AND (
                so.custom_pos_opening_entry IS NULL
                OR so.custom_pos_opening_entry = ''
                OR ope.name IS NULL
                OR ope.status = 'Closed'
              )
        """,
        {"profile": entry.pos_profile, "started": entry.period_start_date},
        as_dict=True,
    )
    return [row.name for row in rows]


def delete_held_orders_for_opening_entry(opening_entry_name):
    """Delete the held Sales Orders a shift close is responsible for.

    That is this session's own held orders, plus any stranded on the same till by an earlier
    session that could never be swept - see _orphaned_held_orders.
    """
    try:
        names = frappe.get_all(
            "Sales Order",
            filters={
                "custom_is_klik_held": 1,
                "docstatus": 0,
                "custom_pos_opening_entry": opening_entry_name,
            },
            pluck="name",
        )
        for name in _orphaned_held_orders(opening_entry_name):
            if name not in names:
                names.append(name)
        # M-Pesa orders this shift's checkouts never let go of (the browser closed mid-sale).
        if frappe.db.has_column("Sales Order", "custom_klik_mpesa_order"):
            for name in frappe.get_all(
                "Sales Order",
                filters={
                    "custom_klik_mpesa_order": 1,
                    "docstatus": 0,
                    "custom_pos_opening_entry": opening_entry_name,
                },
                pluck="name",
            ):
                if name not in names:
                    names.append(name)
        from klik_pos.api.mpesa_order import delete_order, live_push

        deleted = 0
        for name in names:
            # Kept for its push: the payment may still come, or came, for this order. One a
            # checkout never handed over goes on the Held tab, where it can be finished.
            if live_push(name):
                frappe.db.set_value("Sales Order", name, "custom_is_klik_held", 1)
                continue
            try:
                delete_order(frappe.get_doc("Sales Order", name))
                deleted += 1
            except Exception as e:
                frappe.logger().error("Error deleting held order %s: %s", name, e)
        if deleted:
            frappe.logger().info(
                "Cleared %d held order(s) for opening entry %s", deleted, opening_entry_name
            )
        return deleted
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Clear Held Orders on POS Close")
        return 0


COPY_MESSAGE_LABEL = "Copy as Message"
# PowerPack seeds its script under this name; a site's own copy under another name also works.
POWERPACK_COPY_MESSAGE_SCRIPT = "PowerPack - Copy as Message (Sales Order)"
# The till shares held orders (draft Sales Orders) and quotes; nothing else goes through here.
SHARE_DOCTYPES = ("Sales Order", "Quotation", "Sales Invoice")
# The button the script adds, as written in its source: __('Copy as Message') or "...".
_COPY_MESSAGE_BUTTON = re.compile(r"add_custom_button\(\s*(?:__\(\s*)?['\"]Copy as Message['\"]")


def _share_doctype(doctype):
    if doctype not in SHARE_DOCTYPES:
        frappe.throw(frappe._("Sharing is not available for {0}.").format(doctype))
    return doctype


def _copy_as_image_enabled(doctype="Sales Order"):
    """PowerPack's Copy as Image (cecypo_powerpack.copy_as_image), when it is installed and on."""
    if "cecypo_powerpack" not in frappe.get_installed_apps():
        return False
    try:
        from cecypo_powerpack.copy_as_image import DOCTYPES
        from cecypo_powerpack.utils import is_feature_enabled
    except ImportError:
        return False
    return doctype in DOCTYPES and is_feature_enabled("enable_copy_as_image")


def _copy_message_script(doctype="Sales Order"):
    rows = frappe.get_all(
        "Client Script",
        filters={"dt": doctype, "enabled": 1, "view": "Form"},
        fields=["name", "script"],
        order_by="name asc",
    )
    powerpack = f"PowerPack - Copy as Message ({doctype})"
    rows.sort(key=lambda row: row.name != powerpack)
    return next((row.script for row in rows if _COPY_MESSAGE_BUTTON.search(row.script or "")), None)


@frappe.whitelist()
def get_held_order_share_tools(doctype="Sales Order"):
    """What a held order's row offers besides Print: the site's enabled "Copy as Message"
    Client Script for Sales Order (its source, which the SPA runs as the desk form would,
    payment details and all - desk hands the same source to anyone who opens the form), and
    whether PowerPack's Copy as Image is on. `date_format` is what the script's dates use."""
    doctype = _share_doctype(doctype)
    if not frappe.has_permission(doctype, "read"):
        return {"copy_message_script": None, "copy_image": False, "date_format": None}
    return {
        "copy_message_script": _copy_message_script(doctype),
        "copy_image": bool(_copy_as_image_enabled(doctype)),
        "date_format": frappe.db.get_default("date_format") or "dd-mm-yyyy",
    }


@frappe.whitelist()
def get_held_order_share_doc(order_id, doctype="Sales Order"):
    """The held order as the desk form has it, for the Copy as Message script, with its
    currency's symbol for format_currency."""
    doc = frappe.get_doc(_share_doctype(doctype), order_id)
    doc.check_permission("read")
    return {
        "doc": doc.as_dict(convert_dates_to_str=True),
        "currency_symbol": frappe.db.get_value("Currency", doc.currency, "symbol") or doc.currency,
    }

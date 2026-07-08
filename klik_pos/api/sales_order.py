import json

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from klik_pos.api.sales_invoice import (
    _apply_extra_fields,
    _apply_walkin_party_fields,
    _get_active_pos_profile,
    _parse_extra_fields,
    get_current_pos_opening_entry,
    parse_invoice_data,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _assert_held_order_access(so):
    """Ensure the SO is a KLiK held order the current user/session may act on.

    Scoping rules (POS context): the order must belong to the caller's active
    opening entry, or — when there is no active session — have been created by
    the caller. System Managers bypass the check.
    """
    if not so.custom_is_klik_held:
        frappe.throw(_("Order {0} is not a KLiK held order.").format(so.name))

    if "System Manager" in frappe.get_roles():
        return

    opening_entry = get_current_pos_opening_entry()
    if opening_entry:
        if so.custom_pos_opening_entry != opening_entry:
            frappe.throw(_("You are not allowed to access held order {0}.").format(so.name))
    elif so.owner != frappe.session.user:
        frappe.throw(_("You are not allowed to access held order {0}.").format(so.name))


def _build_cart_meta(data, parsed_items, business_type, salesperson, tax_id,
                     delivery_charge, delivery_personnel, sales_and_tax_charges, roundoff_amount):
    """Serialise all POS-specific cart state that doesn't live on the SO itself."""
    raw_discounts = data.get("itemDiscounts", {}) if isinstance(data, dict) else {}
    item_discounts = {}

    for item in parsed_items:
        item_code = item["id"]
        discount_data = raw_discounts.get(item_code) or {}
        if isinstance(discount_data, str):
            try:
                discount_data = json.loads(discount_data)
            except Exception:
                discount_data = {}

        item_discounts[item_code] = {
            "discountPercentage": flt(item.get("discountPercentage") or 0),
            "discountAmount": flt(item.get("discountAmount") or 0),
            "customRate": discount_data.get("customRate"),
            "customRateIncludesTax": bool(discount_data.get("customRateIncludesTax")),
            "bundle_entries": item.get("bundle_entries") or [],
            "item_tax_template": item.get("item_tax_template") or "",
            "item_tax_rate": item.get("item_tax_rate") or {},
        }

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
        "appliedCoupons": data.get("appliedCoupons") or [] if isinstance(data, dict) else [],
        "businessType": business_type or "B2C",
        "salesperson": salesperson,
        "tax_id": tax_id,
        "deliveryCharge": flt(delivery_charge),
        "deliveryPersonnel": delivery_personnel,
        "SalesTaxCharges": sales_and_tax_charges,
        "roundOffAmount": flt(roundoff_amount),
        "customer": customer_data,
        "walkin_name": data.get("walkin_name") if isinstance(data, dict) else None,
        "walkin_phone": data.get("walkin_phone") if isinstance(data, dict) else None,
        "extra_fields": _parse_extra_fields(data),
    }


def _build_sales_order_doc(customer, items, sales_and_tax_charges, cart_meta):
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
    so.selling_price_list = pos_profile.selling_price_list
    so.taxes_and_charges = sales_and_tax_charges or getattr(pos_profile, "taxes_and_charges", "") or ""
    so.custom_pos_profile = pos_profile.name
    so.custom_pos_opening_entry = opening_entry
    so.custom_is_klik_held = 1
    so.custom_klik_cart_meta = json.dumps(cart_meta)

    if cart_meta.get("tax_id") and so.meta.has_field("tax_id"):
        so.tax_id = cart_meta.get("tax_id")
    _apply_walkin_party_fields(
        so,
        walkin_name=cart_meta.get("walkin_name"),
        walkin_phone=cart_meta.get("walkin_phone"),
    )
    _apply_extra_fields(so, cart_meta.get("extra_fields"))

    for item in items:
        so.append("items", {
            "item_code": item["id"],
            "qty": flt(item.get("quantity") or 1),
            "rate": flt(item.get("price") or 0),
            "uom": item.get("uom") or "",
            "delivery_date": nowdate(),
            "warehouse": warehouse,
        })

    so.set_missing_values()
    so.calculate_taxes_and_totals()
    return so


def _rebuild_sales_order(so, customer, items, sales_and_tax_charges, cart_meta):
    """Overwrite an existing held Sales Order with refreshed cart data."""
    pos_profile = _get_active_pos_profile()
    warehouse = getattr(pos_profile, "warehouse", "") or ""

    so.customer = customer
    so.delivery_date = nowdate()
    so.taxes_and_charges = sales_and_tax_charges or getattr(pos_profile, "taxes_and_charges", "") or ""
    so.set("items", [])

    if cart_meta.get("tax_id") and so.meta.has_field("tax_id"):
        so.tax_id = cart_meta.get("tax_id")
    _apply_walkin_party_fields(
        so,
        walkin_name=cart_meta.get("walkin_name"),
        walkin_phone=cart_meta.get("walkin_phone"),
    )
    _apply_extra_fields(so, cart_meta.get("extra_fields"))

    for item in items:
        so.append("items", {
            "item_code": item["id"],
            "qty": flt(item.get("quantity") or 1),
            "rate": flt(item.get("price") or 0),
            "uom": item.get("uom") or "",
            "delivery_date": nowdate(),
            "warehouse": warehouse,
        })

    so.set_missing_values()
    so.calculate_taxes_and_totals()


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

        (
            customer, items, _, sales_and_tax_charges, _, business_type,
            roundoff_amount, delivery_charge, delivery_personnel, _,
            _, _, salesperson, tax_id, _, _,
        ) = parse_invoice_data(data)

        cart_meta = _build_cart_meta(
            data, items, business_type, salesperson, tax_id,
            delivery_charge, delivery_personnel, sales_and_tax_charges, roundoff_amount,
        )

        if target_order_id:
            so = frappe.get_doc("Sales Order", target_order_id)
            if so.docstatus != 0:
                frappe.throw(_("Cannot update held order {0}: it is no longer a draft.").format(target_order_id))
            _rebuild_sales_order(so, customer, items, sales_and_tax_charges, cart_meta)
            so.custom_klik_cart_meta = json.dumps(cart_meta)
            so.save(ignore_permissions=True)
        else:
            so = _build_sales_order_doc(customer, items, sales_and_tax_charges, cart_meta)
            so.insert(ignore_permissions=True)

        return {"success": True, "order_name": so.name}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Create Held Order Error")
        return {"success": False, "message": str(e)}


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

        item_discounts = cart_meta.get("itemDiscounts", {})

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

        items = []
        for row in so.items:
            d = item_discounts.get(row.item_code) or {}
            items.append({
                "item_code": row.item_code,
                "item_name": item_names.get(row.item_code) or row.item_code,
                "quantity": flt(row.qty),
                "price": flt(row.rate),
                "uom": row.uom or "",
                "discountAmount": flt(d.get("discountAmount") or 0),
                "discountPercentage": flt(d.get("discountPercentage") or 0),
                "customRate": d.get("customRate"),
                "customRateIncludesTax": bool(d.get("customRateIncludesTax")),
                "bundle_entries": d.get("bundle_entries") or [],
                "item_tax_template": d.get("item_tax_template") or "",
                "item_tax_rate": d.get("item_tax_rate") or {},
            })

        return {
            "success": True,
            "name": so.name,
            "customer": so.customer,
            "customer_data": cart_meta.get("customer"),
            "walkin_name": cart_meta.get("walkin_name"),
            "walkin_phone": cart_meta.get("walkin_phone"),
            "extra_fields": cart_meta.get("extra_fields") or {},
            "items": items,
            "cart_meta": cart_meta,
            "grand_total": flt(so.grand_total),
            "currency": so.currency,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Held Order Details Error")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def delete_held_order(order_id):
    """Delete a held (draft) Sales Order."""
    try:
        so = frappe.get_doc("Sales Order", order_id)
        _assert_held_order_access(so)
        if so.docstatus != 0:
            return {"success": False, "error": f"Cannot delete {order_id}: not a draft."}
        so.delete(ignore_permissions=True)
        return {"success": True, "message": f"Held order {order_id} deleted."}
    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Order {order_id} not found."}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"Delete Held Order Error: {order_id}")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def get_held_orders(limit=50, start=0, search="", skip_opening_entry_filter=False):
    """List held Sales Orders.

    By default lists held orders for the current POS session (used by the
    Closing Shift page). When ``skip_opening_entry_filter`` is true (used by the
    Invoice History page), the opening-entry restriction is dropped and results
    are scoped by role instead: System Managers/Administrators see all, other
    users see only their own — mirroring ``get_sales_invoices``.
    """
    try:
        if isinstance(skip_opening_entry_filter, str):
            skip_opening_entry_filter = skip_opening_entry_filter.lower() in ("true", "1", "yes")

        limit = int(limit) if limit else 50
        start = int(start) if start else 0
        is_admin_user = "System Manager" in frappe.get_roles() or "Administrator" in frappe.get_roles()

        filters = {"custom_is_klik_held": 1, "docstatus": 0}
        if skip_opening_entry_filter:
            if not is_admin_user:
                filters["owner"] = frappe.session.user
        else:
            opening_entry = get_current_pos_opening_entry()
            if opening_entry:
                filters["custom_pos_opening_entry"] = opening_entry
            elif not is_admin_user:
                # No active session — only show the caller's own held orders
                filters["owner"] = frappe.session.user

        orders = frappe.get_all(
            "Sales Order",
            filters=filters,
            fields=[
                "name", "customer", "customer_name", "transaction_date",
                "grand_total", "currency", "owner", "modified",
                "custom_pos_profile", "custom_pos_opening_entry",
            ],
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
            ]

        order_names = [o.name for o in orders]
        items_map = {}
        if order_names:
            all_items = frappe.get_all(
                "Sales Order Item",
                filters={"parent": ["in", order_names]},
                fields=["parent", "item_code", "item_name", "qty", "rate"],
            )
            for row in all_items:
                items_map.setdefault(row.parent, []).append({
                    "item_code": row.item_code,
                    "item_name": row.item_name,
                    "qty": flt(row.qty),
                    "rate": flt(row.rate),
                })

        # Resolve cashier (owner full name) so rows render/filter like invoices.
        owner_ids = list({o.owner for o in orders if o.owner})
        cashier_map = {}
        if owner_ids:
            for row in frappe.get_all(
                "User", filters={"name": ["in", owner_ids]}, fields=["name", "full_name"]
            ):
                cashier_map[row.name] = row.full_name

        for order in orders:
            order["status"] = "Draft"
            order["items"] = items_map.get(order.name, [])
            order["cashier"] = cashier_map.get(order.owner) or order.owner

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
    try:
        if data and isinstance(data, str):
            data = json.loads(data)

        so = frappe.get_doc("Sales Order", order_id)
        _assert_held_order_access(so)
        if so.docstatus != 0:
            frappe.throw(_("Sales Order {0} is not a draft.").format(order_id))

        # Reuse the full invoice submission pipeline
        from klik_pos.api.sales_invoice import queue_sales_invoice
        result = queue_sales_invoice(data)

        if result.get("success"):
            # SO fulfilled — remove it so it doesn't clutter held orders list
            try:
                frappe.delete_doc("Sales Order", order_id, ignore_permissions=True)
            except Exception as del_err:
                frappe.logger().warning(
                    "Could not delete held SO %s after checkout: %s", order_id, del_err
                )

        return result

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"Checkout Held Order Error: {order_id}")
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Internal — called from pos_entry.py on shift close
# ---------------------------------------------------------------------------

def delete_held_orders_for_opening_entry(opening_entry_name):
    """Delete all held Sales Orders linked to a POS session (called on shift close)."""
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
        deleted = 0
        for name in names:
            try:
                frappe.delete_doc("Sales Order", name, ignore_permissions=True)
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

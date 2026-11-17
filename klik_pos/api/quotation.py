import json

import frappe
from frappe.utils import add_days, add_months, cint, flt, getdate, nowdate

from klik_pos.api.sales_invoice import (
    _apply_extra_fields,
    _apply_pos_tax_treatment,
    _apply_remarks,
    _apply_walkin_party_fields,
    _get_active_pos_profile,
    _parse_extra_fields,
    _resolve_item_tax_details_for_line,
    parse_invoice_data,
    resolve_selling_price_list,
    set_line_price_list,
)
from klik_pos.api.sales_order import _active_till, _apply_order_discount, _build_cart_meta, line_meta
from klik_pos.overrides.loss_of_sale import split_held_items

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _quote_item_row(q, pos_profile, item, warehouse):
    """A quote line: the whole ask, never split into stock and Loss of Sale, since a quote
    reserves nothing and is not limited by stock."""
    row = {
        "item_code": item["id"],
        "qty": (flt(item.get("quantity")) + flt(item.get("los_qty"))) or 1,
        "rate": flt(item.get("price") or 0),
        "uom": item.get("uom") or "",
        "warehouse": warehouse,
    }
    # The cashier's own wording; set_missing_values fills the Item's otherwise.
    if item.get("description"):
        row["description"] = item["description"]
    set_line_price_list(row, "Quotation Item", item.get("price_list"))
    item_tax_template, item_tax_rate = _resolve_item_tax_details_for_line(q, item, pos_profile)
    if item_tax_template:
        row["item_tax_template"] = item_tax_template
    if item_tax_rate:
        row["item_tax_rate"] = frappe.as_json(item_tax_rate)
    return row


def _build_quotation_doc(customer, items, sales_and_tax_charges, cart_meta, order_discount_amount=0.0):
    """A new draft Quotation from parsed cart data; mirrors sales_order._build_sales_order_doc."""
    pos_profile = _get_active_pos_profile()
    warehouse = getattr(pos_profile, "warehouse", "") or ""

    q = frappe.new_doc("Quotation")
    q.quotation_to = "Customer"
    q.party_name = customer
    # The shared helpers read doc.customer, which a Quotation does not have; this is a plain
    # attribute, not a field, so it is never saved.
    q.customer = customer
    q.order_type = "Sales"
    q.transaction_date = nowdate()
    # ERPNext only defaults valid_till in quotation.js on the desk form; mirror it here.
    days = cint(frappe.db.get_single_value("CRM Settings", "default_valid_till"))
    q.valid_till = add_days(q.transaction_date, days) if days else add_months(q.transaction_date, 1)
    q.company = pos_profile.company
    q.currency = pos_profile.currency
    q.selling_price_list = resolve_selling_price_list(cart_meta.get("selling_price_list"), None, pos_profile)
    q.custom_pos_profile = pos_profile.name
    q.custom_klik_cart_meta = json.dumps(cart_meta)
    # The cart already applied Pricing Rules; see sales_order._build_sales_order_doc.
    q.ignore_pricing_rule = 1

    _apply_order_discount(q, pos_profile, order_discount_amount)

    if cart_meta.get("tax_id") and q.meta.has_field("tax_id"):
        q.tax_id = cart_meta.get("tax_id")
    _apply_walkin_party_fields(
        q,
        walkin_name=cart_meta.get("walkin_name"),
        walkin_phone=cart_meta.get("walkin_phone"),
        walkin_tax_id=cart_meta.get("tax_id"),
    )
    _apply_extra_fields(q, cart_meta.get("extra_fields"))
    if cart_meta.get("remarks") is not None:
        _apply_remarks(q, {"remarks": cart_meta.get("remarks")})

    for item in items:
        q.append("items", _quote_item_row(q, pos_profile, item, warehouse))

    pos_line_prices = [(row.item_code, flt(row.rate), flt(row.price_list_rate)) for row in q.items]
    _apply_pos_tax_treatment(q, pos_profile, sales_and_tax_charges, pos_line_prices, restore_price_list_rate=False)
    return q


def _quote_in_use(quote_name):
    """Name of the non-cancelled Sales Invoice made from this quote, else None."""
    return frappe.db.get_value(
        "Sales Invoice",
        {"custom_klik_quotation": quote_name, "docstatus": ["in", [0, 1]]},
        "name",
    )


def _is_expired(valid_till):
    return bool(valid_till) and getdate(nowdate()) > getdate(valid_till)


def _current_list_rate(item_code, uom, price_list):
    """The price list's rate today for this item and uom; the stock-uom rate scaled by the
    uom's conversion if it has none of its own; else 0."""
    def rate(u):
        # Valid today, newest valid_from first; a customer-specific price is not a list price.
        rows = frappe.db.sql(
            """select price_list_rate from `tabItem Price`
            where item_code=%s and price_list=%s and uom=%s and ifnull(customer,'')=''
              and (valid_from is null or valid_from <= %s) and (valid_upto is null or valid_upto >= %s)
            order by valid_from desc limit 1""",
            (item_code, price_list, u, nowdate(), nowdate()),
        )
        return [r[0] for r in rows]

    found = rate(uom)
    if found:
        return flt(found[0])
    stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
    factor = frappe.db.get_value("UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor")
    base = rate(stock_uom) if stock_uom else None
    return flt(base[0]) * flt(factor or 1) if base else 0.0


# ---------------------------------------------------------------------------
# Whitelisted API endpoints
# ---------------------------------------------------------------------------


@frappe.whitelist()
def create_quote(data):
    """Save the POS cart as a submitted Quotation. Not limited by stock."""
    try:
        if isinstance(data, str):
            data = json.loads(data)

        if not cint(_get_active_pos_profile().get("custom_allow_quotes", 1)):
            frappe.throw(frappe._("Quotes are not allowed at this till."))

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

        cart_meta = _build_cart_meta(
            data, items, business_type, salesperson, tax_id,
            delivery_charge, delivery_personnel, sales_and_tax_charges, roundoff_amount,
        )
        order_discount_amount = flt(data.get("orderDiscountAmount") or 0)

        q = _build_quotation_doc(customer, items, sales_and_tax_charges, cart_meta, order_discount_amount)
        q.insert(ignore_permissions=True)
        q.submit()

        # Quotation has no tax_id of its own to survive a save; held orders restore it the same way.
        if tax_id and q.meta.has_field("tax_id") and q.tax_id != tax_id:
            q.db_set("tax_id", tax_id)

        return {"success": True, "quote_name": q.name}

    except Exception as e:
        # Roll back before logging (the Error Log row would be discarded otherwise) and so the
        # naming counter taken by insert() is not committed for a quote that does not exist.
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Create Quote Error")
        return {"success": False, "message": str(e)}


ORDERED = ("Ordered", "Partially Ordered")
QUOTE_STATES = ("open", "converted", "closed", "all")


@frappe.whitelist()
def get_quotes(limit=50, start=0, search="", state="open"):
    """Quotes of the active till's company, newest first, for any till of that company.

    state: open (the default - loadable for sale), converted (invoiced, or ordered),
    closed (Lost or cancelled), or all. Each row carries its state and, when invoiced, the
    invoice.
    """
    try:
        if state not in QUOTE_STATES:
            frappe.throw(frappe._("Unknown quote state {0}.").format(state))
        company = _get_active_pos_profile().company
        from frappe.query_builder import Order
        from pypika.terms import ExistsCriterion

        q, si = frappe.qb.DocType("Quotation"), frappe.qb.DocType("Sales Invoice")
        by_quote = (si.custom_klik_quotation == q.name) & si.docstatus.isin([0, 1])
        invoice = frappe.qb.from_(si).select(si.name).where(by_quote).orderby(si.creation).limit(1)
        invoiced = ExistsCriterion(frappe.qb.from_(si).select(1).where(by_quote))
        converted = invoiced | ((q.docstatus == 1) & q.status.isin(ORDERED))
        closed = converted.negate() & ((q.docstatus == 2) | (q.status == "Lost"))
        is_open = converted.negate() & (q.docstatus == 1) & q.status.notin(["Lost", "Cancelled", *ORDERED])

        query = (
            frappe.qb.from_(q)
            .select(
                q.name, q.party_name.as_("customer"), q.customer_name,
                q.custom_walkin_customer_name.as_("walkin_name"), q.transaction_date, q.valid_till,
                q.grand_total, q.currency, q.status, q.docstatus, invoice.as_("invoice"),
            )
            .where(q.docstatus.isin([1, 2]) & (q.company == company))
            .orderby(q.transaction_date, order=Order.desc)
            .orderby(q.creation, order=Order.desc)
            .offset(cint(start))
            .limit(cint(limit))
        )
        if state != "all":
            query = query.where({"open": is_open, "converted": converted, "closed": closed}[state])
        if search:
            like = f"%{search}%"
            query = query.where(q.name.like(like) | q.customer_name.like(like) | q.custom_walkin_customer_name.like(like))
        rows = query.run(as_dict=True)
        for r in rows:
            r["state"] = (
                "converted" if r.invoice or (r.docstatus == 1 and r.status in ORDERED)
                else "closed" if r.docstatus == 2 or r.status == "Lost"
                else "open"
            )
            r["expired"] = _is_expired(r.valid_till)
        return {"success": True, "data": rows}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Quotes Error")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def get_quote_details(quote_id, as_copy=0):
    """Items, customer and cart metadata to load a quote into the cart; same shape as
    sales_order.get_held_order_details. An expired quote loads at today's list prices.

    as_copy: a converted or closed quote too, as the lines of a new sale - at today's list
    prices and without the quote's discounts; the cart is not linked to it.
    """
    try:
        as_copy = cint(as_copy)
        q = frappe.get_doc("Quotation", quote_id)
        if q.company != _get_active_pos_profile().company or q.docstatus not in ((1, 2) if as_copy else (1,)):
            return {"success": False, "message": frappe._("Quote {0} is not available.").format(quote_id)}
        if not as_copy:
            if q.status in ("Lost", "Cancelled", *ORDERED):
                return {"success": False, "message": frappe._("Quote {0} is not available.").format(quote_id)}
            invoice = _quote_in_use(quote_id)
            if invoice:
                return {"success": False, "message": frappe._("Quote {0} has already been invoiced ({1}).").format(quote_id, invoice)}

        cart_meta = {}
        if q.custom_klik_cart_meta:
            try:
                cart_meta = json.loads(q.custom_klik_cart_meta)
            except Exception:
                pass
        expired = _is_expired(q.valid_till)
        if as_copy:
            # A new sale: the old one's delivery, shipping, remarks and PO stay with it.
            for key in ("deliveryCharge", "deliveryPersonnel", "shipping_rule", "remarks", "extra_fields"):
                cart_meta.pop(key, None)

        names = {
            r.name: r.item_name
            for r in frappe.get_all("Item", filters={"name": ["in", [r.item_code for r in q.items]]}, fields=["name", "item_name"])
        }
        # Loaded for sale: split against the stock at this till now, as a reopened held order is.
        lines = [
            {
                "id": row.item_code,
                "quantity": flt(row.qty),
                "los_qty": 0,
                "uom": row.uom,
                "bundle_entries": line_meta(cart_meta, i, row.item_code).get("bundle_entries") or [],
            }
            for i, row in enumerate(q.items)
        ]
        split_held_items(lines, _active_till())

        items = []
        for i, (row, line) in enumerate(zip(q.items, lines)):
            d = line_meta(cart_meta, i, row.item_code)
            fresh = expired or as_copy
            items.append({
                "item_code": row.item_code,
                "item_name": names.get(row.item_code) or row.item_code,
                "quantity": line["quantity"],
                "los_qty": line["los_qty"],
                "price": _current_list_rate(row.item_code, row.uom, q.selling_price_list) if fresh else flt(row.rate),
                "uom": row.uom or "",
                "discountAmount": 0 if fresh else flt(d.get("discountAmount") or 0),
                "discountPercentage": 0 if fresh else flt(d.get("discountPercentage") or 0),
                "customRate": None if fresh else d.get("customRate"),
                "customRateIncludesTax": False if fresh else bool(d.get("customRateIncludesTax")),
                "bundle_entries": d.get("bundle_entries") or [],
                "item_tax_template": d.get("item_tax_template") or "",
                "item_tax_rate": d.get("item_tax_rate") or {},
                "description": d.get("description") or "",
                "selectedPriceList": d.get("selectedPriceList") or "",
            })

        return {
            "success": True,
            "name": q.name,
            "customer": q.party_name,
            "customer_data": cart_meta.get("customer"),
            "walkin_name": cart_meta.get("walkin_name"),
            "walkin_phone": cart_meta.get("walkin_phone"),
            "extra_fields": cart_meta.get("extra_fields") or {},
            "shipping_rule": cart_meta.get("shipping_rule"),
            "selling_price_list": q.selling_price_list,
            "items": items,
            "cart_meta": cart_meta,
            "remarks": "" if as_copy else (q.get("remarks") or ""),
            "grand_total": flt(q.grand_total),
            "currency": q.currency,
            "discount_amount": 0 if as_copy else flt(q.discount_amount or 0),
            "apply_discount_on": q.apply_discount_on or "",
            "expired": expired,
            "copy": bool(as_copy),
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Quote Details Error")
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Email (Quote Saved dialog)
# ---------------------------------------------------------------------------


def _unique_emails(values):
	seen, out = set(), []
	for v in values:
		v = (v or "").strip()
		if v and v.lower() not in seen:
			seen.add(v.lower())
			out.append(v)
	return out


@frappe.whitelist()
def get_quote_emails(quote_name):
	"""Prefill for the quote's email box: its contact's address, then the customer's."""
	q = frappe.get_doc("Quotation", quote_name)
	q.check_permission("read")
	customer_email = (
		frappe.db.get_value("Customer", q.party_name, "email_id") if q.quotation_to == "Customer" else None
	)
	return _unique_emails([q.contact_email, customer_email])


@frappe.whitelist()
def send_quote_email(quote_name, emails):
	"""Email the quote as a PDF to the comma/semicolon-separated addresses, using the till's
	Email Template (POS Profile custom_email_template) when one is set."""
	from frappe.utils import validate_email_address

	recipients = _unique_emails(str(emails or "").replace(";", ",").split(","))
	if not recipients:
		frappe.throw("Enter an email address")
	bad = [e for e in recipients if not validate_email_address(e)]
	if bad:
		frappe.throw(f"Not an email address: {', '.join(bad)}")

	q = frappe.get_doc("Quotation", quote_name)
	q.check_permission("read")
	from klik_pos.api.email import render_email

	subject, message = render_email(
		q,
		template=_get_active_pos_profile().get("custom_email_template"),
		fallback_subject=f"Quotation {q.name} from {q.company}",
		fallback_message=(
			f"<p>Dear {q.customer_name},</p><p>Please find attached quotation <b>{q.name}</b>"
			f" for {frappe.utils.fmt_money(q.rounded_total or q.grand_total, currency=q.currency)}.</p>"
			"<p>Thank you.</p>"
		),
	)
	pdf = frappe.get_print("Quotation", q.name, as_pdf=True)
	frappe.sendmail(
		recipients=recipients,
		subject=subject,
		message=message,
		attachments=[{"fname": f"{q.name}.pdf", "fcontent": pdf}],
		reference_doctype="Quotation",
		reference_name=q.name,
		delayed=False,
	)
	return {"recipients": recipients}

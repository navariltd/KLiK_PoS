import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, today

from erpnext.stock.doctype.batch.batch import get_batch_qty
from klik_pos.klik_pos.utils import get_current_pos_profile, get_current_pos_profile_lite

from ..sql_builder import apply_sql_permissions
from .cost import COST_FIELD, HIDDEN, LAST_PURCHASE, VALUATION, cost_mode, last_purchase_rates
from .item_price import allowed_price_list_sql, fetch_item_price, get_price_list_with_customer_priority


@frappe.whitelist()
def get_batch_nos_with_qty(item_code):
    pos_doc = get_current_pos_profile()
    warehouse = pos_doc.warehouse

    # Not batch-tracked, nothing to find - and Batch read is not every cashier's to have.
    if not item_code or not warehouse or not frappe.db.get_value("Item", item_code, "has_batch_no"):
        return []

    batches = frappe.get_list(
        "Batch",
        filters={"item": item_code},
        fields=["name", "batch_id", "expiry_date"],
    )

    batch_qty_data = []
    for b in batches:
        qty = get_batch_qty(batch_no=b.name, warehouse=warehouse)
        if qty > 0:
            batch_qty_data.append({"batch_id": b.batch_id, "qty": qty})

    return batch_qty_data


@frappe.whitelist()
def get_item_uoms_and_prices(item_code, customer=None):
    if not item_code:
        return {}

    try:
        price_list = get_price_list_with_customer_priority(customer)

        item_list = frappe.get_list(
            "Item",
            filters={"name": item_code},
            fields=["stock_uom", "item_name"],
            limit=1,
        )

        if not item_list:
            return {}

        item_data = item_list[0]

        uom_entries = frappe.get_list(
            "UOM Conversion Detail",
            filters={"parent": item_code},
            fields=["uom", "conversion_factor"],
            ignore_permissions=True,
        )

        uom_data = []
        uom_names_in_table = set()

        for uom_row in uom_entries:
            uom_names_in_table.add(uom_row.uom)
            uom_data.append(
                {
                    "uom": uom_row.uom,
                    "conversion_factor": uom_row.conversion_factor,
                    "price": 0.0,
                }
            )

        stock_uom = item_data.stock_uom

        if stock_uom and stock_uom not in uom_names_in_table:
            uom_data.insert(
                0,
                {
                    "uom": stock_uom,
                    "conversion_factor": 1.0,
                    "price": 0.0,
                },
            )

        for uom_info in uom_data:
            price_sql = f"""
                SELECT price_list_rate
                FROM `tabItem Price`
                WHERE item_code = %s AND uom = %s AND selling = 1 AND {allowed_price_list_sql()}
                AND (price_list = %s OR %s IS NULL OR %s = '')
                ORDER BY CASE WHEN price_list = %s THEN 0 ELSE 1 END, modified DESC
                LIMIT 1
            """
            price_sql = apply_sql_permissions(price_sql)

            price = frappe.db.sql(
                price_sql,
                (
                    item_code,
                    uom_info["uom"],
                    price_list,
                    price_list,
                    price_list,
                    price_list,
                ),
            )

            if price:
                uom_info["price"] = flt(price[0][0])
            else:
                base_price_info = fetch_item_price(
                    item_code,
                    price_list=price_list,
                    customer=customer,
                    uom=stock_uom,
                )

                # No selling price stays 0: never the valuation rate (cost, which a till may hide).
                if base_price_info and flt(base_price_info.get("price", 0)) > 0:
                    uom_info["price"] = (
                        flt(base_price_info["price"])
                        * uom_info["conversion_factor"]
                    )

        result = {
            "base_uom": stock_uom,
            "uoms": uom_data,
            "price_list_used": price_list,
        }

        return result

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Get Item UOMs Error for {item_code}",
        )
        return {
            "base_uom": None,
            "uoms": [],
            "price_list_used": None,
        }


@frappe.whitelist()
def get_serial_nos_for_item(item_code: str):
    # The till asks whenever it does not know the item's tracking. Not serial-tracked, nothing
    # to find - and Serial No read is not every cashier's to have.
    if not item_code or not frappe.db.get_value("Item", item_code, "has_serial_no"):
        return []

    try:
        pos_doc = get_current_pos_profile()
        warehouse = getattr(pos_doc, "warehouse", None)

        filters = {"item_code": item_code, "status": "Active"}

        if warehouse:
            filters["warehouse"] = warehouse

        serials = frappe.get_list(
            "Serial No",
            filters=filters,
            fields=["name", "serial_no"],
            limit=500,
            order_by="modified desc",
        )

        return [{"serial_no": s.serial_no or s.name} for s in serials]

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Get Serial Nos Error for {item_code}",
        )
        return []


def _current_valuation_rate(warehouse_map, fallback):
    """Current valuation per stock UOM: each warehouse's latest ledger rate, weighted by the
    stock it holds. Summing stock_value_difference instead drifts from the real stock value
    once a Stock Reconciliation is in the history."""
    stocked = [row for row in warehouse_map.values() if flt(row.get("bal_qty")) > 0]
    total_qty = sum(flt(row.get("bal_qty")) for row in stocked)
    if not total_qty:
        return flt(fallback)
    return sum(flt(row.get("bal_qty")) * flt(row.get("val_rate")) for row in stocked) / total_qty


@frappe.whitelist()
def get_full_pricing_and_batch_details(
    item_code, warehouse=None, customer=None
):
    if not item_code:
        return {}

    item_list = frappe.get_list(
        "Item",
        filters={"name": item_code},
        fields=[
            "item_name",
            "item_code",
            "standard_rate",
            "valuation_rate",
            "stock_uom",
            "brand",
            "description",
            "image",
            "has_batch_no",
            "has_serial_no",
        ],
        limit=1,
    )

    if not item_list:
        return {}

    item_info = item_list[0]

    today_date = getdate(today())
    to_datetime = get_datetime(str(today_date) + " 23:59:59")

    bins = frappe.db.sql(
        """
        SELECT warehouse, actual_qty, stock_value, valuation_rate
        FROM `tabBin`
        WHERE item_code = %s
        """,
        (item_code,),
        as_dict=True,
    )

    global_stock_total = {
        "total_qty": sum(flt(b.actual_qty) for b in bins),
        "total_value": sum(flt(b.stock_value) for b in bins),
        "avg_valuation_rate": sum(flt(b.valuation_rate) for b in bins) / len(bins) if bins else 0,
    }
    stock_by_warehouse = sorted(
        ({"warehouse": b.warehouse, "qty": flt(b.actual_qty)} for b in bins if flt(b.actual_qty)),
        key=lambda row: -row["qty"],
    )

    price_query = f"""
        SELECT price_list, price_list_rate AS rate, currency, uom, customer, valid_from, valid_upto, name
        FROM `tabItem Price`
        WHERE item_code = %s AND selling = 1 AND {allowed_price_list_sql()}
        AND (customer = %s OR customer IS NULL OR customer = '')
    """
    price_query = apply_sql_permissions(price_query)

    price_list_records = frappe.db.sql(
        price_query,
        (item_code, customer),
        as_dict=True,
    )

    sle_query = """
        SELECT warehouse, actual_qty, stock_value_difference, valuation_rate,
               qty_after_transaction, voucher_type, batch_no, serial_no,
               serial_and_batch_bundle, has_batch_no, has_serial_no
        FROM `tabStock Ledger Entry`
        WHERE item_code = %s AND docstatus < 2 AND is_cancelled = 0
        AND posting_datetime <= %s
    """

    params = [item_code, to_datetime]

    if warehouse:
        sle_query += " AND warehouse = %s"
        params.append(warehouse)

    sle_query += " ORDER BY posting_datetime ASC, creation ASC"
    sle_query = apply_sql_permissions(sle_query)

    sle_entries = frappe.db.sql(
        sle_query,
        tuple(params),
        as_dict=True,
    )

    warehouse_map = {}
    latest_valuation_rate = flt(item_info.valuation_rate)
    serial_map = {}
    bundle_map = {}

    for entry in sle_entries:
        wh = entry.warehouse

        if wh not in warehouse_map:
            warehouse_map[wh] = {
                "bal_qty": 0.0,
                "bal_val": 0.0,
                "val_rate": 0.0,
            }

        qty_diff = flt(entry.actual_qty)

        if (
            entry.voucher_type == "Stock Reconciliation"
            and not any([
                entry.batch_no,
                entry.serial_no,
                entry.serial_and_batch_bundle,
            ])
        ):
            qty_diff = (
                flt(entry.qty_after_transaction)
                - flt(warehouse_map[wh]["bal_qty"])
            )

        warehouse_map[wh]["bal_qty"] += qty_diff
        warehouse_map[wh]["bal_val"] += flt(entry.stock_value_difference)

        if entry.valuation_rate:
            warehouse_map[wh]["val_rate"] = flt(entry.valuation_rate)
            latest_valuation_rate = flt(entry.valuation_rate)

        if entry.serial_no:
            for sn in entry.serial_no.split("\n"):
                sn = sn.strip()
                if sn:
                    if sn not in serial_map:
                        serial_map[sn] = {
                            "qty": 0.0,
                            "warehouse": wh,
                            "val_rate": 0.0,
                            "batch_no": entry.batch_no,
                            "bundle_id": entry.serial_and_batch_bundle,
                        }

                    serial_map[sn]["qty"] += qty_diff

                    if entry.valuation_rate:
                        serial_map[sn]["val_rate"] = flt(entry.valuation_rate)

                    if serial_map[sn]["warehouse"] != wh:
                        serial_map[sn]["warehouse"] = wh

        if entry.serial_and_batch_bundle:
            bundle_id = entry.serial_and_batch_bundle
            if bundle_id not in bundle_map:
                bundle_map[bundle_id] = {
                    "warehouse": wh,
                    "batch_no": entry.batch_no,
                    "serial_nos": [],
                    "qty": 0,
                }

            if not bundle_map[bundle_id]["serial_nos"]:
                bundle_details = frappe.db.sql("""
                    SELECT serial_no, batch_no, qty
                    FROM `tabSerial and Batch Entry`
                    WHERE parent = %s
                """, (bundle_id,), as_dict=True)

                for detail in bundle_details:
                    if detail.serial_no:
                        for sn in detail.serial_no.split("\n"):
                            sn = sn.strip()
                            if sn:
                                bundle_map[bundle_id]["serial_nos"].append(sn)
                                if sn not in serial_map:
                                    serial_map[sn] = {
                                        "qty": 0.0,
                                        "warehouse": wh,
                                        "val_rate": 0.0,
                                        "batch_no": detail.batch_no or entry.batch_no,
                                        "bundle_id": bundle_id,
                                    }
                                serial_map[sn]["qty"] += flt(detail.qty) * qty_diff
                    elif detail.batch_no:
                        bundle_map[bundle_id]["batch_no"] = detail.batch_no

            bundle_map[bundle_id]["qty"] += abs(qty_diff)

    if not sle_entries and not frappe.has_permission("Stock Ledger Entry", "read"):
        # A till role may read Bin but not the ledger: stock as it stands now, so the cart
        # panel does not say 'No stock available' for an item on the shelf.
        for b in bins:
            if not warehouse or b.warehouse == warehouse:
                warehouse_map[b.warehouse] = {
                    "bal_qty": flt(b.actual_qty),
                    "bal_val": flt(b.stock_value),
                    "val_rate": flt(b.valuation_rate),
                }

    total_bal_qty = sum(v["bal_qty"] for v in warehouse_map.values())
    total_bal_val = sum(v["bal_val"] for v in warehouse_map.values())

    effective_val_rate = _current_valuation_rate(warehouse_map, latest_valuation_rate)

    batch_map = {}
    result_batches = []

    if item_info.has_batch_no:
        batch_sql = """
            SELECT warehouse, batch_no, SUM(actual_qty) AS qty, SUM(stock_value_difference) AS val
            FROM `tabStock Ledger Entry`
            WHERE item_code = %s AND docstatus < 2 AND is_cancelled = 0 
            AND batch_no != '' AND posting_datetime <= %s
        """

        b_params = [item_code, to_datetime]

        if warehouse:
            batch_sql += " AND warehouse = %s"
            b_params.append(warehouse)

        batch_sql += " GROUP BY batch_no, warehouse"

        bundle_sql = """
            SELECT sle.warehouse, sbe.batch_no, SUM(sbe.qty) AS qty, SUM(sbe.stock_value_difference) AS val
            FROM `tabStock Ledger Entry` sle
            JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sle.serial_and_batch_bundle
            WHERE sle.item_code = %s AND sle.docstatus < 2 AND sle.is_cancelled = 0 
            AND sle.has_batch_no = 1 AND sle.posting_datetime <= %s
        """

        if warehouse:
            bundle_sql += " AND sle.warehouse = %s"

        bundle_sql += " GROUP BY sbe.batch_no, sle.warehouse"

        batch_sql = apply_sql_permissions(batch_sql)
        bundle_sql = apply_sql_permissions(bundle_sql)

        entries = frappe.db.sql(
            batch_sql,
            tuple(b_params),
            as_dict=True,
        ) + frappe.db.sql(
            bundle_sql,
            tuple(b_params),
            as_dict=True,
        )

        for row in entries:
            key = (row.batch_no, row.warehouse)
            if key not in batch_map:
                batch_map[key] = {"qty": 0.0, "val": 0.0}
            batch_map[key]["qty"] += flt(row.qty)
            batch_map[key]["val"] += flt(row.val)

    # Only a batch-tracked item has batches to list - and Batch read is not every cashier's.
    batches_raw = (
        frappe.get_list(
            "Batch",
            filters={"item": item_code},
            fields=["name", "expiry_date", "manufacturing_date"],
        )
        if item_info.has_batch_no
        else []
    )

    for b in batches_raw:
        for (bid, wh), bdata in batch_map.items():
            if bid == b.name and flt(bdata["qty"]) > 0:
                qty = flt(bdata["qty"])

                batch_serials = [
                    {"serial_no": sn, "warehouse": s["warehouse"], "val_rate": s["val_rate"]}
                    for sn, s in serial_map.items()
                    if s.get("batch_no") == bid and s["qty"] > 0
                ]

                result_batches.append(
                    {
                        "batch_id": b.name,
                        "qty": qty,
                        "val": flt(bdata["val"]),
                        "val_rate": (
                            flt(bdata["val"]) / qty
                            if qty
                            else effective_val_rate
                        ),
                        "expiry_date": b.expiry_date,
                        "manufacturing_date": b.manufacturing_date,
                        "warehouse": wh,
                        "serials": batch_serials,
                    }
                )

    active_prices = []

    for p in price_list_records:
        if (
            (p.valid_from and getdate(p.valid_from) > today_date)
            or (
                p.valid_upto
                and getdate(p.valid_upto) < today_date
            )
        ):
            continue

        note_sql = "SELECT note FROM `tabItem Price` WHERE name = %s"
        note_sql = apply_sql_permissions(note_sql)

        note_data = frappe.db.sql(note_sql, (p.name,)) or frappe.db.sql(
            apply_sql_permissions(
                "SELECT note FROM `tabItem Price` WHERE name = %s"
            ),
            (p.name,),
        )

        rate = flt(p.rate)
        margin = rate - effective_val_rate

        active_prices.append(
            {
                "price_list": p.price_list,
                "rate": rate,
                "currency": p.currency,
                "uom": p.uom,
                "customer": p.customer,
                "note": note_data[0][0] if note_data else None,
                "cost": effective_val_rate,
                "margin": margin,
                "margin_pct": (
                    (margin / effective_val_rate * 100)
                    if effective_val_rate
                    else 0
                ),
            }
        )

    serials_list = []
    if not item_info.has_batch_no:
        for sn, s_data in serial_map.items():
            if s_data["qty"] > 0:
                serial_entry = {
                    "serial_no": sn,
                    "warehouse": s_data["warehouse"],
                    "val_rate": s_data["val_rate"] or effective_val_rate,
                }
                serials_list.append(serial_entry)

    details = {
        "item_name": item_info.item_name,
        "item_code": item_info.item_code,
        "standard_rate": item_info.standard_rate or 0,
        "valuation_rate": effective_val_rate,
        "uom": item_info.stock_uom,
        "brand": item_info.brand,
        "description": item_info.description,
        "image": item_info.image,
        "has_batch_no": item_info.has_batch_no,
        "has_serial_no": item_info.has_serial_no,
        "global_stock_total": global_stock_total,
        "stock_by_warehouse": stock_by_warehouse,
        "total_bal_qty": total_bal_qty,
        "total_bal_val": total_bal_val,
        "price_lists": sorted(active_prices, key=lambda p: (p["price_list"] or "").lower()),
        "batches": result_batches if item_info.has_batch_no else [],
        "serials": serials_list,
        "warehouse_stock": [
            {
                "warehouse": wh,
                "bal_qty": flt(v["bal_qty"]),
                "bal_val": flt(v["bal_val"]),
                "val_rate": flt(v["val_rate"]),
            }
            for wh, v in warehouse_map.items()
            if v["bal_qty"] != 0
        ],
    }
    return _with_cost_display(item_code, details)


def _with_cost_display(item_code, details):
    """details as the till's Cost Display allows: stripped when Hidden (hidden on screen is
    not enough when the response carries it), else with the last purchase cost added."""
    mode, company = _till_cost_mode()
    if mode == HIDDEN:
        return _without_cost(details)
    rate, date = last_purchase_rates([item_code], company).get(item_code, (0, None))
    details.update(cost_display=mode, last_purchase_rate=rate, last_purchase_date=date)
    if mode == LAST_PURCHASE:
        # Price-list margins are worked from the cost the till shows.
        for price in details["price_lists"]:
            margin = price["rate"] - rate
            price.update(cost=rate, margin=margin, margin_pct=(margin / rate * 100) if rate else 0)
    return details


def _till_cost_mode():
    """(Cost Display, company) of the caller's till. No till (a desk user, who sees valuation
    in ERPNext anyway): Valuation, in the default company."""
    queued = len(frappe.local.message_log)
    try:
        profile = get_current_pos_profile_lite([COST_FIELD, "company"])
    except frappe.ValidationError:
        del frappe.local.message_log[queued:]
        return VALUATION, frappe.defaults.get_user_default("Company")
    return cost_mode(profile), profile.get("company")


def _without_cost(details):
    """get_full_pricing_and_batch_details' answer with every valuation, stock value, cost and
    margin set to 0; selling prices and quantities stay."""
    details["valuation_rate"] = 0
    details["total_bal_val"] = 0
    details["global_stock_total"].update(total_value=0, avg_valuation_rate=0)
    for price in details.get("price_lists") or []:
        price.update(cost=0, margin=0, margin_pct=0)
    for batch in details.get("batches") or []:
        batch.update(val=0, val_rate=0)
        for serial in batch.get("serials") or []:
            serial["val_rate"] = 0
    for serial in details.get("serials") or []:
        serial["val_rate"] = 0
    for row in details.get("warehouse_stock") or []:
        row.update(bal_val=0, val_rate=0)
    return details


@frappe.whitelist()
def get_customer_sales_history(customer=None, walkin_phone=None, limit=20):
    """Recent Sales Invoices for a customer (regular) or walk-in phone, plus summary.

    Lazy endpoint: only called on explicit user action in the POS item modal.
    `customer` takes precedence over `walkin_phone`. Walk-in match uses the
    `custom_walkin_phone` field so individual walk-ins are not lumped together.
    """
    customer = (customer or "").strip()
    walkin_phone = (walkin_phone or "").strip()

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))

    empty = {
        "rows": [],
        "summary": {
            "order_count": 0,
            "total_spent": 0,
            "last_purchase_date": None,
            "avg_order_value": 0,
            "currency": None,
        },
        "matched_by": None,
    }

    if customer:
        condition = "si.customer = %(value)s"
        value = customer
        matched_by = "customer"
    elif walkin_phone:
        if not frappe.db.has_column("Sales Invoice", "custom_walkin_phone"):
            empty["matched_by"] = "walkin_phone"
            return empty
        condition = "si.custom_walkin_phone = %(value)s"
        value = walkin_phone
        matched_by = "walkin_phone"
    else:
        return empty

    rows = frappe.db.sql(
        f"""
        SELECT si.name, si.posting_date, si.grand_total, si.status, si.currency
        FROM `tabSales Invoice` si
        WHERE {condition} AND si.docstatus = 1
        ORDER BY si.posting_date DESC, si.creation DESC
        LIMIT %(limit)s
        """,
        {"value": value, "limit": limit},
        as_dict=True,
    )

    agg = frappe.db.sql(
        f"""
        SELECT COUNT(*) AS order_count,
               COALESCE(SUM(si.grand_total), 0) AS total_spent,
               MAX(si.posting_date) AS last_purchase_date
        FROM `tabSales Invoice` si
        WHERE {condition} AND si.docstatus = 1
        """,
        {"value": value},
        as_dict=True,
    )[0]

    order_count = agg.order_count or 0
    total_spent = agg.total_spent or 0
    currency = rows[0].currency if rows else None

    return {
        "rows": rows,
        "summary": {
            "order_count": order_count,
            "total_spent": total_spent,
            "last_purchase_date": agg.last_purchase_date,
            "avg_order_value": (total_spent / order_count) if order_count else 0,
            "currency": currency,
        },
        "matched_by": matched_by,
    }
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# POS Profile feature-toggle fields that are NOT covered by the auto-synced
# custom/pos_profile.json customization. These were previously created by a
# one-time patch (allow_price_list_switching) or not created at all
# (allow_warehouse_change), so they silently go missing on DB restores or when
# a patch is skipped. Re-asserting them here (idempotently) on every migrate
# makes them self-heal.
POS_PROFILE_FEATURE_FIELDS = [
    {
        "fieldname": "allow_price_list_switching",
        "label": "Allow Price List Switching",
        "fieldtype": "Check",
        "insert_after": "allow_zero_rate_sales",
        "description": "Allow cashiers to switch selling price lists in Klik POS.",
        "default": "0",
        "module": "KLiK PoS",
    },
    {
        "fieldname": "allow_warehouse_change",
        "label": "Allow Warehouse Change",
        "fieldtype": "Check",
        "insert_after": "allow_price_list_switching",
        "description": "Allow cashiers to switch the selling warehouse in Klik POS.",
        "default": "0",
        "module": "KLiK PoS",
    },
    {
        "fieldname": "custom_enable_sales_lens",
        "label": "Show Customer Sales Lens",
        "fieldtype": "Check",
        "insert_after": "allow_warehouse_change",
        "description": "Show a customer purchase-history snapshot tab in the POS item details modal.",
        "default": "0",
        "module": "KLiK PoS",
    },
    {
        "fieldname": "custom_show_overdue_warning",
        "label": "Show Overdue Invoice Warning",
        "fieldtype": "Check",
        "insert_after": "custom_enable_sales_lens",
        "description": "Show a warning popup when selecting a customer with overdue invoices in the POS.",
        "default": "0",
        "module": "KLiK PoS",
    },
    {
        "fieldname": "custom_allow_credit_sales_as_pos",
        "label": "Allow Credit Sales as POS Sales",
        "fieldtype": "Check",
        "insert_after": "custom_show_overdue_warning",
        "description": (
            "When enabled, Credit Sale invoices are marked as POS sales "
            "(is_pos=1) even though they're unpaid at creation. When "
            "disabled (default), Credit Sales are always is_pos=0."
        ),
        "default": "0",
        "module": "KLiK PoS",
    },
]


def install_pos_profile_feature_fields():
    """Idempotent and collision-safe. Only creates feature fields the POS Profile
    doctype does not already have (as a STANDARD or custom field) — some stacks
    ship `allow_warehouse_change` as a standard field, and creating a Custom Field
    with a colliding name raises. Returns the list of fieldnames actually created.
    Safe to run on every migrate."""
    missing = [
        f for f in POS_PROFILE_FEATURE_FIELDS
        if not frappe.db.has_column("POS Profile", f["fieldname"])
    ]
    if missing:
        create_custom_fields({"POS Profile": missing}, update=True)
    return [f["fieldname"] for f in missing]


def install_pos_extra_fields_child():
    """Create the `POS Extra Field` child doctype and the `custom_pos_extra_fields`
    Table custom field on POS Profile. Idempotent and safe on every migrate."""
    if not frappe.db.exists("DocType", "POS Extra Field"):
        child = frappe.new_doc("DocType")
        child.update({
            "name": "POS Extra Field",
            "module": "KLiK PoS",
            "custom": 1,
            "istable": 1,
            "fields": [
                {
                    "fieldname": "so_si_commonfield",
                    "label": "SO/SI Common Field",
                    "fieldtype": "Select",
                    "description": "Common field in Sales Order / Sales Invoice",
                    "in_list_view": 1,
                    "reqd": 1,
                },
                {
                    "fieldname": "reqd",
                    "label": "Required",
                    "fieldtype": "Check",
                    "in_list_view": 1,
                    "default": "0",
                },
            ],
            "permissions": [],
        })
        child.insert(ignore_permissions=True)

    # Column break so the extra-fields table sits in its own column (more width),
    # within the same section as the price-list / warehouse toggles.
    if not frappe.db.exists("Custom Field", {"dt": "POS Profile", "fieldname": "custom_pos_extra_fields_cb"}):
        create_custom_fields({
            "POS Profile": [{
                "fieldname": "custom_pos_extra_fields_cb",
                "label": "",
                "fieldtype": "Column Break",
                "insert_after": "allow_warehouse_change",
                "module": "KLiK PoS",
            }]
        }, update=True)

    if not frappe.db.exists("Custom Field", {"dt": "POS Profile", "fieldname": "custom_pos_extra_fields"}):
        create_custom_fields({
            "POS Profile": [{
                "fieldname": "custom_pos_extra_fields",
                "label": "POS Extra Fields",
                "fieldtype": "Table",
                "options": "POS Extra Field",
                "insert_after": "custom_pos_extra_fields_cb",
                "description": "Extra SO/SI common fields to capture in the POS Additional Info dialog.",
                "module": "KLiK PoS",
            }]
        }, update=True)


def ensure_pos_extra_fields_child():
    """Hook-safe wrapper. Never abort migrate on failure."""
    try:
        install_pos_extra_fields_child()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "klik_pos: POS Extra Field child install failed")


def install_mpesa_reconciled_payment_child():
    """Create the `POS Mpesa Reconciled Payment` child doctype and the
    `custom_mpesa_reconciled_payments` Table custom field on Sales Invoice.
    Idempotent and safe on every migrate.

    Traceability-only record of which `Mpesa C2B Payment Register` row(s)
    were reconciled onto a given Sales Invoice via the POS "Add Selected
    Payments" flow (`klik_pos.api.mpesa.process_mpesa`).
    `mpesa_c2b_payment_register` is stored as plain Data (not a Link) so this
    child table has zero schema dependency on the `frappe_mpsa_payments` app
    being installed/reachable at display time.
    """
    if not frappe.db.exists("DocType", "POS Mpesa Reconciled Payment"):
        child = frappe.new_doc("DocType")
        child.update({
            "name": "POS Mpesa Reconciled Payment",
            "module": "KLiK PoS",
            "custom": 1,
            "istable": 1,
            "fields": [
                {
                    "fieldname": "mpesa_c2b_payment_register",
                    "label": "Mpesa C2B Payment Register",
                    "fieldtype": "Data",
                    "description": "Name of the reconciled Mpesa C2B Payment Register row (plain text, not a Link).",
                    "in_list_view": 1,
                    "reqd": 1,
                },
                {
                    "fieldname": "transid",
                    "label": "Transaction ID",
                    "fieldtype": "Data",
                    "in_list_view": 1,
                },
                {
                    "fieldname": "amount",
                    "label": "Amount",
                    "fieldtype": "Currency",
                    "in_list_view": 1,
                },
                {
                    "fieldname": "msisdn",
                    "label": "Phone Number",
                    "fieldtype": "Data",
                    "in_list_view": 1,
                },
                {
                    "fieldname": "mode_of_payment",
                    "label": "Mode of Payment",
                    "fieldtype": "Link",
                    "options": "Mode of Payment",
                    "in_list_view": 1,
                },
                {
                    "fieldname": "excess_payment_entry",
                    "label": "Excess Payment Entry",
                    "fieldtype": "Data",
                    "description": "Name of the unallocated Payment Entry holding this receipt's overpaid excess as reusable customer credit (plain text, not a Link).",
                    "read_only": 1,
                },
            ],
            "permissions": [],
        })
        child.insert(ignore_permissions=True)
    else:
        child = frappe.get_doc("DocType", "POS Mpesa Reconciled Payment")
        _missing_child_fields = [
            {
                "fieldname": "mode_of_payment",
                "label": "Mode of Payment",
                "fieldtype": "Link",
                "options": "Mode of Payment",
                "in_list_view": 1,
            },
            {
                "fieldname": "excess_payment_entry",
                "label": "Excess Payment Entry",
                "fieldtype": "Data",
                "description": "Name of the unallocated Payment Entry holding this receipt's overpaid excess as reusable customer credit (plain text, not a Link).",
                "read_only": 1,
            },
        ]
        existing = {f.fieldname for f in child.fields}
        appended = False
        for field_def in _missing_child_fields:
            if field_def["fieldname"] not in existing:
                child.append("fields", field_def)
                appended = True
        if appended:
            child.save(ignore_permissions=True)

    if not frappe.db.exists(
        "Custom Field", {"dt": "Sales Invoice", "fieldname": "custom_mpesa_reconciled_payments"}
    ):
        create_custom_fields({
            "Sales Invoice": [{
                "fieldname": "custom_mpesa_reconciled_payments",
                "label": "Mpesa Reconciled Payments",
                "fieldtype": "Table",
                "options": "POS Mpesa Reconciled Payment",
                "insert_after": "payments",
                "description": "Mpesa C2B Payment Register rows reconciled onto this invoice via the POS M-Pesa Payment Options flow.",
                "module": "KLiK PoS",
                "read_only": 1,
            }]
        }, update=True)


def ensure_mpesa_reconciled_payment_child():
    """Hook-safe wrapper. Never abort migrate on failure."""
    try:
        install_mpesa_reconciled_payment_child()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "klik_pos: POS Mpesa Reconciled Payment child install failed")


def ensure_pos_profile_feature_fields():
    """Hook entrypoint for after_migrate / after_install. Never abort on failure."""
    try:
        install_pos_profile_feature_fields()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "klik_pos: POS Profile feature-field install failed")
    ensure_pos_extra_fields_child()
    ensure_mpesa_reconciled_payment_child()

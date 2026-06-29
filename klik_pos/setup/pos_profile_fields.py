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


def ensure_pos_profile_feature_fields():
    """Hook entrypoint for after_migrate / after_install. Never abort on failure."""
    try:
        install_pos_profile_feature_fields()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "klik_pos: POS Profile feature-field install failed")
    ensure_pos_extra_fields_child()

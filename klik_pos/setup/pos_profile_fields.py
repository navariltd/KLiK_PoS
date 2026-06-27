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


def ensure_pos_profile_feature_fields():
    """Hook entrypoint for after_migrate / after_install. Never abort on failure."""
    try:
        install_pos_profile_feature_fields()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "klik_pos: POS Profile feature-field install failed")

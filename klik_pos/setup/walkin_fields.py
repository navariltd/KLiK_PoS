import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

WALKIN_DOCTYPES = ["Quotation", "Sales Order", "Sales Invoice", "Delivery Note"]

# Same fieldnames on every doctype -> get_mapped_doc auto-copies across conversions.
WALKIN_FIELDS = [
    {
        "fieldname": "custom_walkin_customer_name",
        "label": "Walk-in Customer Name",
        "fieldtype": "Data",
        "insert_after": "tax_id",
        "in_standard_filter": 1,
        "no_copy": 0,
        "translatable": 0,
        "module": "KLiK PoS",
    },
    {
        "fieldname": "custom_walkin_phone",
        "label": "Walk-in Phone",
        "fieldtype": "Data",
        "insert_after": "custom_walkin_customer_name",
        "in_standard_filter": 1,
        "no_copy": 0,
        "translatable": 0,
        "module": "KLiK PoS",
    },
]


def install_walkin_fields(doctypes=None):
    """Idempotent. create_custom_fields() updates/creates as needed."""
    doctypes = doctypes or WALKIN_DOCTYPES
    custom_fields = {dt: WALKIN_FIELDS for dt in doctypes}
    create_custom_fields(custom_fields, ignore_validate=True)
    return doctypes


@frappe.whitelist()
def install_walkin_party_fields():
    """POS Profile button entrypoint. Installs walk-in fields on all four selling doctypes."""
    if not frappe.has_permission("Custom Field", "create"):
        frappe.throw(
            _("You do not have permission to create custom fields."),
            frappe.PermissionError,
        )

    installed = install_walkin_fields(WALKIN_DOCTYPES)
    frappe.db.commit()
    return {"installed_on": installed}

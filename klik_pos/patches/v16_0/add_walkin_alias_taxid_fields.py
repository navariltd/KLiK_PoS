"""One-time patch: adds the fields needed to capture a walk-in ("Cash Customer")
sale's Alias (name) alongside the existing Tax ID, and to let both be corrected
after the invoice has already been submitted.

Wire this in by adding one line under the [post_model_sync] section of
klik_pos/patches.txt, alongside the other v16_0 custom-field patches already there:

	klik_pos.patches.v16_0.add_walkin_alias_taxid_fields

Then run:

	bench --site <your-site> migrate

Safe to re-run: create_custom_fields() no-ops on any field that already exists,
and make_property_setter() simply overwrites the same property to the same value.

Fields/settings added, all on Sales Invoice:
  - custom_customer_alias: the walk-in customer's name/alias for this one sale.
    Same "walk-in only" convention as the existing standard tax_id field (see
    TaxSection.tsx on the frontend, which gates it to selectedCustomer.isWalkin).
    Not on the Customer doctype -- "Cash Customer" is one shared record reused
    by every walk-in, so a name/PIN captured here is per-transaction, not a
    property of that shared record.
  - custom_walkin_info_change_log: hidden Long Text holding a JSON array log of
    every post-submit edit to alias/tax_id (old value, new value, who, when) --
    written by klik_pos.api.sales_invoice.update_walkin_customer_info. Kept as
    a single JSON field rather than a child table specifically so it can be
    updated with a plain frappe.db.set_value() alongside the two data fields,
    with no need to load/append/save the parent document.
  - allow_on_submit = 1 on BOTH the new custom_customer_alias field and the
    core tax_id field. Frappe locks every field on a submitted document unless
    it's explicitly marked allow_on_submit -- without this, update_walkin_
    customer_info's frappe.db.set_value() call would still work (it bypasses
    document validation entirely), but leaving the schema itself saying "this
    can never change post-submit" would be misleading to anyone using
    Customize Form / the Desk UI later. tax_id is a CORE field, not a Custom
    Field, so it needs a Property Setter rather than a create_custom_fields
    entry -- that's what make_property_setter does below.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	create_custom_fields(
		{
			"Sales Invoice": [
				{
					"fieldname": "custom_customer_alias",
					"label": "Customer Name (Alias)",
					"fieldtype": "Data",
					"insert_after": "tax_id",
					"allow_on_submit": 1,
					"description": (
						"Optional name for a walk-in (Cash Customer) sale, captured at checkout "
						"and editable later even after the invoice is submitted. Per-transaction "
						"only -- does not change the shared Cash Customer record."
					),
				},
				{
					"fieldname": "custom_walkin_info_change_log",
					"label": "Walk-in Info Change Log",
					"fieldtype": "Long Text",
					"insert_after": "custom_customer_alias",
					"allow_on_submit": 1,
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
					"description": (
						"JSON array log of post-submit edits to custom_customer_alias/tax_id -- "
						"written by klik_pos.api.sales_invoice.update_walkin_customer_info. Not "
						"meant to be edited directly."
					),
				},
			],
		}
	)

	make_property_setter(
		"Sales Invoice", "tax_id", "allow_on_submit", 1, "Check", validate_fields_for_doctype=False
	)

	frappe.clear_cache(doctype="Sales Invoice")
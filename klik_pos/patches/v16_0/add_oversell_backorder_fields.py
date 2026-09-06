"""One-time patch: adds the two custom fields the oversell/backorder feature needs.

Wire this in by adding one line under the [post_model_sync] section of
klik_pos/patches.txt, alongside the other v16_0 custom-field patches already there:

	klik_pos.patches.v16_0.add_oversell_backorder_fields

Then run:

	bench --site <your-site> migrate

Safe to re-run: create_custom_fields() no-ops on any field that already exists.

Fields added:
  - Item.custom_allow_oversell: per-item opt-in for selling past zero stock, used when
    the POS Profile's global custom_allow_out_of_stock_sale flag is OFF.
  - Sales Invoice Item.custom_is_backorder_row: internal marker stamped by
    klik_pos.api.sales_invoice._split_oversold_items on the synthetic line it creates
    for an oversold item's shortfall. Not meant to be user-facing -- it's hidden, and
    exists purely so later code (stock/reservation validation, and the post-submit
    backorder handoff) can tell a real stock-backed row apart from one that's
    deliberately carrying no stock behind it yet.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "custom_allow_oversell",
					"label": "Allow Oversell in POS",
					"fieldtype": "Check",
					"insert_after": "is_stock_item",
					"default": "0",
					"description": (
						"When checked, this item can still be sold in Klik POS after stock hits "
						"zero -- the shortfall is tracked as a backorder and fulfilled from the "
						"next Purchase Receipt. Ignored if the POS Profile's own "
						"'Allow Out of Stock Sale' is checked, which allows it for every item."
					),
				}
			],
			"Sales Invoice Item": [
				{
					"fieldname": "custom_is_backorder_row",
					"label": "Is Backorder Row",
					"fieldtype": "Check",
					"insert_after": "warehouse",
					"default": "0",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
				}
			],
		}
	)
	frappe.clear_cache(doctype="Item")
	frappe.clear_cache(doctype="Sales Invoice Item")

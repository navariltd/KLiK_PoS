"""One-time patch: adds a shadow copy of the walk-in ("Cash Customer") Tax ID
that survives Sales Invoice's own doc.submit() -- and gives KLiK's eTIMS PIN
override (klik_pos/integrations/etims_walkin_pin.py) a stable, per-invoice
value to read the walk-in's PIN from at submit time.

Why this field exists (context for anyone re-reading this later):

The core `tax_id` field gets silently reset to the Customer master's tax_id
(blank, for the shared walk-in "Cash Customer" record) by Sales Invoice's own
validate()/set_missing_values(), which reruns as part of doc.submit() itself
-- BEFORE the on_submit hook chain fires, which is why KLiK already has to
force tax_id back on with db_set() *after* submit (see the long comments at
queue_sales_invoice/process_queued_sales_invoice in api/sales_invoice.py).

The Kenya Compliance (eTIMS) app's on_submit hook runs its own eTIMS-payload
build DURING that same on_submit chain -- i.e. strictly before KLiK's
post-submit db_set() ever runs -- so by the time it reads tax_id, the walk-in
PIN is already gone, and KLiK's post-submit db_set() fixing the *displayed*
tax_id happens too late to matter to that payload. On top of that, the eTIMS
app's payload builder never reads Sales Invoice.tax_id in the first place --
it reads Customer.tax_id from the database directly, which for the shared
walk-in customer is permanently blank.

custom_walkin_tax_id sidesteps both problems: it's a plain custom field core
ERPNext knows nothing about (same proven pattern as the existing
custom_customer_alias field -- see add_walkin_alias_taxid_fields.py), so
doc.submit()'s validate() pass never touches it, and it's always set
in-memory *before* submit (in build_sales_invoice_doc), so it's present and
correct by the time the on_submit hook chain -- KLiK's eTIMS PIN override
included -- runs.

Wire this in by adding one line under the [post_model_sync] section of
klik_pos/patches.txt, alongside add_walkin_alias_taxid_fields:

	klik_pos.patches.v16_0.add_walkin_tax_id_etims_override_field

Then run:

	bench --site <your-site> migrate

Safe to re-run: create_custom_fields() no-ops on any field that already exists.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Sales Invoice": [
				{
					"fieldname": "custom_walkin_tax_id",
					"label": "Walk-in Tax ID (eTIMS override, internal)",
					"fieldtype": "Data",
					"insert_after": "custom_walkin_info_change_log",
					"allow_on_submit": 1,
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
					"description": (
						"Internal shadow copy of the walk-in Tax ID, used only so the eTIMS PIN "
						"override (klik_pos/integrations/etims_walkin_pin.py) has a value that "
						"survives doc.submit(). Kept in sync with tax_id by "
						"klik_pos.api.sales_invoice -- not meant to be edited directly."
					),
				},
			],
		}
	)

	frappe.clear_cache(doctype="Sales Invoice")
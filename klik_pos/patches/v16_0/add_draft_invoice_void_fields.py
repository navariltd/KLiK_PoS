"""One-time patch: adds the fields needed to VOID a leftover draft Sales
Invoice instead of deleting it.

Wire this in by adding one line under the [post_model_sync] section of
klik_pos/patches.txt, alongside the other v16_0 custom-field patches already
there:

	klik_pos.patches.v16_0.add_draft_invoice_void_fields

Then run:

	bench --site <your-site> migrate

Safe to re-run: create_custom_fields() no-ops on any field that already
exists.

Why this exists: Kenyan tax record-keeping (KRA/eTIMS) expects every
generated invoice number to stay traceable. Physically deleting a draft
Sales Invoice removes its row (and its number) from the table entirely --
during an audit that's indistinguishable from a hidden/erased sale, even
though the row was only ever an abandoned cart that was never submitted (no
GL or Stock Ledger entries were ever posted for it). So instead of
`doc.delete()`, klik_pos.api.sales_invoice.delete_draft_invoice() and
delete_draft_invoices_for_opening_entry() now flag the draft with these
fields and leave the row in place forever -- full customer, items, amounts,
and invoice number all still there for an auditor to inspect, just marked
as closed out rather than "still open and needing action".

Fields added, all on Sales Invoice:
  - custom_pos_voided: Check. The flag itself. A voided draft is still
    docstatus=0 (Draft) and status "Draft" -- voiding does not submit,
    cancel, or otherwise change its accounting status, since it never had
    one (Drafts never post GL/Stock Ledger entries). This field is purely
    "the cashier/closing-shift flow is done with this row".
  - custom_pos_void_reason: Small Text. Free text describing why (e.g.
    "Left open at POS close" for the bulk closing-shift sweep, or "Voided
    by cashier" for a manual single-invoice void).
  - custom_pos_voided_by: Link (User). Who voided it.
  - custom_pos_voided_on: Datetime. When it was voided.

None of these are allow_on_submit -- a voided row is never submitted, so
that concern doesn't apply here the way it does for custom_customer_alias.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Sales Invoice": [
				{
					"fieldname": "custom_pos_voided",
					"label": "Voided (POS Draft)",
					"fieldtype": "Check",
					"insert_after": "custom_pos_opening_entry",
					"default": "0",
					"no_copy": 1,
					"print_hide": 1,
					"description": (
						"Set when a leftover draft invoice is voided instead of deleted, "
						"either individually or via the Closing Shift 'Void all drafts' "
						"action. The row is kept permanently for audit/KRA record-keeping "
						"-- this flag only marks it as resolved so it stops being counted "
						"as an open item."
					),
				},
				{
					"fieldname": "custom_pos_void_reason",
					"label": "Void Reason",
					"fieldtype": "Small Text",
					"insert_after": "custom_pos_voided",
					"no_copy": 1,
					"print_hide": 1,
					"depends_on": "eval:doc.custom_pos_voided",
				},
				{
					"fieldname": "custom_pos_voided_by",
					"label": "Voided By",
					"fieldtype": "Link",
					"options": "User",
					"insert_after": "custom_pos_void_reason",
					"no_copy": 1,
					"print_hide": 1,
					"read_only": 1,
					"depends_on": "eval:doc.custom_pos_voided",
				},
				{
					"fieldname": "custom_pos_voided_on",
					"label": "Voided On",
					"fieldtype": "Datetime",
					"insert_after": "custom_pos_voided_by",
					"no_copy": 1,
					"print_hide": 1,
					"read_only": 1,
					"depends_on": "eval:doc.custom_pos_voided",
				},
			],
		}
	)

	frappe.clear_cache(doctype="Sales Invoice")
import frappe

from klik_pos.api.sales_invoice import validate_required_salesperson


def validate_sales_person_on_submit(doc, method=None):
    """
    Check if Sales Person is required for POS transactions and validate before submitting the Sales Invoice
    """
    validate_required_salesperson(doc)


def block_submit_of_voided_draft(doc, method=None):
    """
    A draft voided at POS close (custom_pos_voided) must stay void forever --
    that's the whole point of the "void instead of delete" design (see
    delete_draft_invoices_for_opening_entry / delete_draft_invoice in
    klik_pos.api.sales_invoice): a voided draft is deliberately left out of
    every shift's payment reconciliation on the assumption it was abandoned
    and will never become a real sale.

    The KLiK PoS app's own screens already hide Edit/Submit for a voided
    draft, but that's a frontend-only guard. Anyone with Sales Invoice submit
    permission can still open the document directly in the Frappe Desk UI
    (edit the posting date/time, then click Submit) and bypass the app
    entirely. This hook enforces the same rule server-side, on the actual
    Document.submit() call, so it applies no matter which path is used to
    submit -- the Desk UI's own Submit button, or klik_pos.api.sales_invoice.
    submit_draft_invoice() -- since both end up calling doc.submit(), which
    always runs before_submit hooks.
    """
    if doc.get("custom_pos_voided"):
        frappe.throw(
            frappe._(
                "This draft was voided when its POS session was closed and can no "
                "longer be submitted. It has been intentionally excluded from that "
                "session's payment reconciliation. If this sale is still valid, "
                "ring it up again as a new sale under a currently open POS session."
            )
        )
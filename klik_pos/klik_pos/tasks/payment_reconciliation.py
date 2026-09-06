"""Hourly customer payment reconciliation for ERPNext 16.

Install this module inside:
    apps/klik_pos/klik_pos/klik_pos/tasks/payment_reconciliation.py

The job only creates standard ERPNext "Process Payment Reconciliation"
documents. ERPNext's own reconciliation worker performs the allocations.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt


DEFAULT_BATCH_SIZE = 100
ACTIVE_PROCESS_STATUSES = ("Queued", "Running")


def hourly_customer_payment_reconciliation() -> None:
    """Queue FIFO reconciliation for customers with unallocated receipts.

    This method is safe to run repeatedly. Fully allocated payments disappear
    from the candidate query, while an existing Queued/Running process prevents
    the same customer/account from being queued twice.
    """

    if not _automation_enabled():
        return

    batch_size = cint(
        frappe.conf.get("klik_auto_reconcile_batch_size", DEFAULT_BATCH_SIZE)
    ) or DEFAULT_BATCH_SIZE
    excluded_customers = set(
        frappe.conf.get("klik_auto_reconcile_excluded_customers")
        or ["1 Cash Customer"]
    )

    candidates = frappe.get_all(
        "Payment Entry",
        filters={
            "docstatus": 1,
            "payment_type": "Receive",
            "party_type": "Customer",
            "unallocated_amount": [">", 0],
        },
        fields=[
            "name",
            "company",
            "party",
            "paid_from as receivable_account",
            "posting_date",
        ],
        order_by="posting_date asc, creation asc",
        limit_page_length=batch_size,
    )

    # One reconciliation process per company/customer/receivable account.
    groups: dict[tuple[str, str, str], dict] = {}
    for payment in candidates:
        if not payment.party or payment.party in excluded_customers:
            continue
        if not payment.receivable_account:
            _log_skip(payment.name, "Payment has no receivable account (paid_from).")
            continue
        groups.setdefault(
            (payment.company, payment.party, payment.receivable_account), payment
        )

    queued = 0
    errors = 0

    for (company, customer, account), payment in groups.items():
        try:
            if _process_already_active(company, customer, account):
                continue
            if not _has_outstanding_invoice(company, customer, account):
                continue

            process = frappe.get_doc(
                {
                    "doctype": "Process Payment Reconciliation",
                    "company": company,
                    "party_type": "Customer",
                    "party": customer,
                    "receivable_payable_account": account,
                }
            )

            # ERPNext versions may expose these optional filters. Leave them
            # empty so all eligible outstanding invoices/payments are included.
            process.insert(ignore_permissions=True)
            process.submit()
            frappe.db.commit()
            queued += 1
        except Exception:
            frappe.db.rollback()
            errors += 1
            frappe.log_error(
                title=f"KLiK auto reconciliation failed: {customer}",
                message=frappe.get_traceback(),
            )

    if queued or errors:
        frappe.logger("klik_payment_reconciliation").info(
            "Hourly reconciliation scan finished: queued=%s errors=%s candidates=%s",
            queued,
            errors,
            len(candidates),
        )


def _automation_enabled() -> bool:
    """Enabled by default; set site_config key to 0 for an emergency stop."""

    return cint(frappe.conf.get("klik_auto_reconcile_enabled", 1)) == 1


def _process_already_active(company: str, customer: str, account: str) -> bool:
    return bool(
        frappe.db.exists(
            "Process Payment Reconciliation",
            {
                "docstatus": 1,
                "company": company,
                "party_type": "Customer",
                "party": customer,
                "receivable_payable_account": account,
                "status": ["in", ACTIVE_PROCESS_STATUSES],
            },
        )
    )


def _has_outstanding_invoice(company: str, customer: str, account: str) -> bool:
    precision = cint(frappe.db.get_single_value("System Settings", "currency_precision"))
    threshold = 0.5 / (10 ** (precision or 2))

    invoice = frappe.db.get_value(
        "Sales Invoice",
        {
            "docstatus": 1,
            "company": company,
            "customer": customer,
            "debit_to": account,
            "outstanding_amount": [">", threshold],
        },
        ["name", "outstanding_amount"],
        as_dict=True,
    )
    return bool(invoice and flt(invoice.outstanding_amount) > threshold)


def _log_skip(payment_entry: str, reason: str) -> None:
    frappe.logger("klik_payment_reconciliation").warning(
        "Skipped Payment Entry %s: %s", payment_entry, reason
    )
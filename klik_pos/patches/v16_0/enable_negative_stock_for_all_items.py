"""One-time patch: turns on ERPNext's own negative-stock settings globally, so every
stock item -- batch-tracked or not -- can be sold past zero/available stock.

Wire this in by adding one line under the [post_model_sync] section of
klik_pos/patches.txt, alongside the other v16_0 patches already there:

	klik_pos.patches.v16_0.enable_negative_stock_for_all_items

Then run:

	bench --site <your-site> migrate

Safe to re-run: both writes are idempotent single-value sets.

What this sets, and why both are needed:

  - Stock Settings.allow_negative_stock = 1
    erpnext.stock.stock_ledger.is_negative_stock_allowed() is an OR across this
    Stock Settings value and each Item's own "Allow Negative Stock" checkbox --
    setting it here at the Stock Settings level covers every item globally, so no
    per-Item backfill is needed. This is what lets a plain, non-batch stock item's
    Bin actual_qty go negative on a normal Sales Invoice submit.

  - Stock Settings.allow_negative_stock_for_batch = 1
    A secondary, narrower check (used for backdated entries and repost/cancel
    consistency -- see erpnext.stock.doctype.serial_and_batch_bundle
    .serial_and_batch_bundle.throw_negative_batch/allow_negative_stock_for_batch)
    that falls back to this Stock Settings value when a Batch doesn't set its own
    override. Setting it globally here is a belt-and-suspenders complement to the
    klik_pos.overrides.serial_and_batch_bundle.CustomSerialAndBatchBundle override
    (registered in hooks.py's extend_doctype_class), which is what actually makes
    negative stock work for batch-tracked items on a normal submit -- see that
    override's module docstring for exactly why the Stock Settings values alone are
    NOT enough for batch items without it (confirmed against ERPNext 16's own test
    suite, not just its source).
"""

import frappe


def execute():
	frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
	frappe.db.set_single_value("Stock Settings", "allow_negative_stock_for_batch", 1)
	frappe.clear_cache(doctype="Stock Settings")
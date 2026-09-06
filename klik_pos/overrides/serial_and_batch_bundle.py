"""Makes ERPNext's own negative-stock settings actually apply to batch-tracked items.

Background (see the removal note above klik_pos.api.sales_invoice._split_oversold_items
for the full history): Klik POS lets every stock item oversell -- sell past what's
actually on hand -- rather than block the sale. For plain, non-batch items, ERPNext 16's
own "Allow Negative Stock" (Stock Settings, or per-Item) is enough on its own: it's
checked in erpnext.stock.stock_ledger.is_negative_stock_allowed() and correctly threaded
through to the Bin-level negative-qty check.

For BATCH-tracked items, it is not enough, and this is a real gap in ERPNext 16 itself,
not a configuration mistake:

erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle.SerialandBatchBundle
.validate() (called on every insert/submit of a Serial and Batch Bundle -- exactly what
happens for every batch-tracked Sales Invoice line) calls:

    self.set_incoming_rate()

with NO arguments. set_incoming_rate()'s signature is
`set_incoming_rate(self, parent=None, row=None, save=False, allow_negative_stock=False, ...)`,
so this call always runs with allow_negative_stock hardcoded False -- regardless of
Stock Settings.allow_negative_stock, Stock Settings.allow_negative_stock_for_batch,
Item.allow_negative_stock, or Batch.allow_negative_stock_for_batch. That flows into
validate_negative_batch(), which throws BatchNegativeStockError unconditionally (its
only exception is an exact-match Stock Reconciliation valuation adjustment).

This is confirmed by ERPNext's own test suite, not just by reading the source: in
erpnext/stock/doctype/serial_and_batch_bundle/test_serial_and_batch_bundle.py,
test_historical_negative_batch_stock_does_not_block_outward has to
`patch.object(SerialandBatchBundle, "validate_negative_batch")` to make a negative-batch
scenario succeed -- even after turning on both Stock Settings flags via
_allow_negative_stock_temporarily(). If the core test suite needs a mock to get past this
check, the documented settings alone were never going to be enough.

This override closes that gap deliberately and permanently, the same way the core test
does it, instead of leaving Klik POS to fabricate provisional Stock Reconciliations and
placeholder batches (with an invented expiry date) to work around it -- see the removed
_ensure_stock_for_item/_auto_provision_stock_for_items in git history.

Wired in via klik_pos/hooks.py: extend_doctype_class -> "Serial and Batch Bundle".

Caveat inherited from ERPNext itself, not introduced by this override: negative-stock
valuation can be imprecise (Stock Settings' own field description for
allow_negative_stock_for_batch warns of this) until a real Purchase Receipt/Invoice for
the item lands and corrects it. That's an accepted tradeoff of negative-stock accounting
in general, not specific to this override.
"""

import frappe
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	SerialandBatchBundle,
)


class CustomSerialAndBatchBundle(SerialandBatchBundle):
	def validate_negative_batch(self, batch_no, available_qty):
		# Klik POS: every item may be sold past zero/available stock, batch-tracked or
		# not (see module docstring for why core's own negative-stock settings don't
		# reach this particular check on their own). Log the dip instead of blocking it,
		# so a negative batch balance is still visible/auditable the way core would have
		# surfaced it as an error.
		if available_qty < 0:
			frappe.logger("klik_pos.negative_stock").info(
				f"Allowing negative stock for batch {batch_no} (item {self.item_code}, "
				f"warehouse {self.warehouse}): available qty after this transaction {available_qty}"
			)
		return
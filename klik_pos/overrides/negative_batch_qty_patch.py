"""Monkey-patch: makes the Batch.batch_qty roll-up stop hard-blocking negative
batch stock, the same way klik_pos.overrides.serial_and_batch_bundle does for
the (separate) Serial and Batch Bundle validation path.

Why this can't be done with hooks.py's extend_doctype_class (like the other
overrides in this app): the check this patches is not a doctype controller
method at all -- it's a bare module-level function,
erpnext.stock.serial_batch_bundle.throw_negative_batch_validation(batch_no, qty),
called directly (unqualified) from another module-level function in the same
file, update_batch_qty(). There is no class/doctype to extend here, so the
only way to intercept it is to replace the function object on the module
itself, before update_batch_qty() ever runs.

What update_batch_qty() does and why it matters for Klik POS's "sell past
zero/available stock" behaviour:

    erpnext.controllers.stock_controller.StockController.make_sl_entries()
    -- called on every stock-updating Sales Invoice submit -- posts the Stock
    Ledger Entries for the transaction, then unconditionally calls
    update_batch_qty(self.doctype, self.name, self.docstatus). That function
    re-derives each affected batch's cached Batch.batch_qty field from the
    posted ledger entries and, if the new value is negative, calls
    throw_negative_batch_validation() -- which does a plain frappe.throw(),
    aborting the whole submit with "Negative Stock Error: The Batch ... has
    negative batch quantity ...".

Critically, this check has NO negative-stock setting gate at all -- not
Stock Settings.allow_negative_stock, not allow_negative_stock_for_batch, not
Item.allow_negative_stock, not Batch.allow_negative_stock_for_batch. Reading
erpnext/stock/serial_batch_bundle.py's update_batch_qty() top to bottom, the
only condition that skips the throw is via_landed_cost_voucher=True (a Landed
Cost Voucher repost), which does not apply to a normal Sales Invoice submit.
So this fires regardless of any of the documented settings -- it is a
genuine, unconditional gap in ERPNext 16 core, not a configuration mistake,
exactly like the SerialAndBatchBundle.validate() gap that
klik_pos.overrides.serial_and_batch_bundle.CustomSerialAndBatchBundle already
closes. It is a *different* check on a *different* field (the cached
Batch.batch_qty roll-up, versus that other override's available_qty check
during Serial and Batch Bundle validation), which is why both overrides are
needed side by side.

This patch replaces throw_negative_batch_validation with a version that logs
the dip (so it's still visible/auditable, same convention used everywhere
else in Klik POS's oversell handling) and returns, instead of throwing. The
caller, update_batch_qty(), still proceeds to
frappe.db.set_value("Batch", batch, "batch_qty", current_qty) right after,
so the batch's cached quantity is correctly recorded as negative -- exactly
the value ERPNext's own message suggests fixing via "Recalculate Batch Qty"
once real stock is received.

Applied from klik_pos/__init__.py (see that file), so it runs once per
Python process -- web worker, background worker, console, or `bench
migrate` -- before any Sales Invoice submission can reach update_batch_qty().
Wrapped in try/except so that if a future ERPNext version renames or removes
this function, Klik POS still loads (falling back to core's own stricter
behaviour for this one check) instead of breaking app installation entirely.
"""

import frappe


def apply():
	try:
		from erpnext.stock import serial_batch_bundle as _sbb
	except Exception:
		frappe.logger("klik_pos.negative_stock").warning(
			"klik_pos.overrides.negative_batch_qty_patch: could not import "
			"erpnext.stock.serial_batch_bundle -- Batch.batch_qty negative-stock "
			"patch was NOT applied. ERPNext's own 'Negative Stock Error' for "
			"Batch quantities may still block sales."
		)
		return

	if not hasattr(_sbb, "throw_negative_batch_validation"):
		frappe.logger("klik_pos.negative_stock").warning(
			"klik_pos.overrides.negative_batch_qty_patch: "
			"erpnext.stock.serial_batch_bundle.throw_negative_batch_validation "
			"no longer exists (ERPNext version change?) -- patch NOT applied."
		)
		return

	def _log_instead_of_throw(batch_no, qty):
		frappe.logger("klik_pos.negative_stock").info(
			f"Allowing negative Batch.batch_qty for batch {batch_no}: recalculated "
			f"qty after this transaction is {qty}."
		)

	_sbb.throw_negative_batch_validation = _log_instead_of_throw
__version__ = "0.0.1"

# Klik POS: patch ERPNext core's Batch.batch_qty negative-stock check so it
# logs instead of blocking, consistent with the rest of this app's
# sell-past-zero-stock behaviour. This particular check is a bare
# module-level function (not a doctype controller method), so it can't be
# reached via hooks.py's extend_doctype_class the way the Serial and Batch
# Bundle override is -- it has to be monkey-patched directly, once, here,
# before any Sales Invoice submission can reach it. See
# klik_pos/overrides/negative_batch_qty_patch.py for the full explanation of
# what this closes and why it's unconditional in core regardless of any
# negative-stock setting.
try:
	from klik_pos.overrides.negative_batch_qty_patch import apply as _apply_negative_batch_qty_patch

	_apply_negative_batch_qty_patch()
except Exception:
	# Never let a patching failure prevent the app itself from loading --
	# worst case, ERPNext's own stricter Batch.batch_qty check stays active.
	import frappe as _frappe

	_frappe.logger("klik_pos.negative_stock").warning(
		"klik_pos: failed to apply negative_batch_qty_patch at app load time.",
		exc_info=True,
	)
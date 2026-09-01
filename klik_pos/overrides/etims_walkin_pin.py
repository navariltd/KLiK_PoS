"""Make the Kenya Compliance (eTIMS) app send the Tax ID/PIN actually entered
at KLiK checkout, instead of always reading the shared walk-in "Cash
Customer" record's tax_id -- which is permanently blank, so a walk-in
buyer's PIN currently never reaches eTIMS no matter what the cashier types.

Root cause (verified against navariltd/kenya-compliance-via-slade,
version-16 branch, and this fork's own klik_pos/api/sales_invoice.py):

1. kenya_compliance_via_slade.kenya_compliance_via_slade.utils.build_invoice_payload
   sets "customer_pin" from `frappe.get_value("Customer", invoice.customer,
   "tax_id")` -- a fresh database read of the CUSTOMER record. It never looks
   at Sales Invoice.tax_id at all, walk-in or not.
2. Even if it did, it wouldn't help: Sales Invoice's own validate(), which
   reruns as part of doc.submit(), resets tax_id back to the Customer
   master's (blank) value before the on_submit hook chain -- which is where
   this app's eTIMS payload gets built -- ever runs. This is exactly why
   klik_pos.api.sales_invoice already has to force tax_id back on with
   db_set() *after* submit for the value to show up on the printed invoice
   at all -- and that fix runs too late to affect the eTIMS payload, which
   is built synchronously during submit, before that db_set() call.

This module does NOT edit kenya_compliance_via_slade's files on disk -- it
patches the one function responsible for the eTIMS payload,
build_invoice_payload, at process boot, entirely from within klik_pos. It
reads the walk-in PIN from Sales Invoice.custom_walkin_tax_id, a plain
custom field (added by
klik_pos.patches.v16_0.add_walkin_tax_id_etims_override_field) that core
ERPNext's validate() never touches -- the same proven trick already used for
custom_customer_alias -- so it is still correct in-memory by the time
on_submit fires, even though the real tax_id field has already been wiped.

Applies to BOTH immediate and background-queued submission (KLiK's "Submit
Invoice in Background" option): doc.submit() runs inside a background worker
process for queued invoices, not just web request workers, so the patch is
applied at Python import time (from klik_pos/__init__.py) rather than tied to
a web-request hook -- that way every process type (web worker, background
worker, scheduler) picks it up once, on first import, regardless of which
kind of process ends up calling doc.submit().

Safe by construction if the compliance app isn't installed, or if a future
release of it renames/restructures build_invoice_payload: every failure mode
here is caught and logged to stdout (never raised), so KLiK PoS itself never
fails to start because of this file. Check the bench worker/web logs for a
line starting with "[klik_pos]" if the eTIMS PIN override ever seems to stop
working after an app update.
"""

_PATCHED_FLAG = "_klik_pos_etims_walkin_pin_patched"

# Every module in kenya_compliance_via_slade that imports build_invoice_payload
# by name (`from ...utils import build_invoice_payload`) binds its OWN separate
# reference at that module's import time. Patching utils.build_invoice_payload
# alone does not reach those already-bound names -- each one has to be patched
# individually. Verified against version-16 as of writing; if a future release
# adds another call site, add its dotted path here too.
_MODULES_THAT_IMPORT_BUILD_INVOICE_PAYLOAD = (
	"kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.shared_overrides",
	"kenya_compliance_via_slade.kenya_compliance_via_slade.apis.remote_response_status_handlers",
)


def apply_walkin_pin_override():
	try:
		import kenya_compliance_via_slade.kenya_compliance_via_slade.utils as etims_utils
	except ImportError:
		# Kenya Compliance (eTIMS) app isn't installed on this site -- nothing to do.
		return

	if getattr(etims_utils, _PATCHED_FLAG, False):
		return  # Already patched this process -- keep this idempotent.

	original_build_invoice_payload = etims_utils.build_invoice_payload

	def _patched_build_invoice_payload(invoice, settings_name):
		payload = original_build_invoice_payload(invoice, settings_name)
		override_pin = _resolve_checkout_pin(invoice)
		if override_pin:
			payload["customer_pin"] = override_pin
		return payload

	etims_utils.build_invoice_payload = _patched_build_invoice_payload
	setattr(etims_utils, _PATCHED_FLAG, True)

	for module_path in _MODULES_THAT_IMPORT_BUILD_INVOICE_PAYLOAD:
		_patch_reference_in(module_path, _patched_build_invoice_payload)


def _patch_reference_in(module_path, patched_fn):
	try:
		import importlib

		module = importlib.import_module(module_path)
		if hasattr(module, "build_invoice_payload"):
			module.build_invoice_payload = patched_fn
		else:
			print(
				f"[klik_pos] eTIMS walk-in PIN override: {module_path} no longer "
				"has a build_invoice_payload attribute -- the compliance app may "
				"have changed shape; override not applied there."
			)
	except Exception as e:  # pragma: no cover - defensive, must never break app boot
		print(f"[klik_pos] eTIMS walk-in PIN override: could not patch {module_path}: {e}")


def _resolve_checkout_pin(invoice):
	"""Priority: the PIN actually captured at THIS checkout, first non-blank wins.

	custom_walkin_tax_id survives doc.submit()'s reset; tax_id is checked too
	in case it happened not to be wiped (e.g. nothing to override). If neither
	is set, return None so the caller falls back to whatever
	build_invoice_payload already resolved -- unchanged behaviour for every
	normal, already-registered customer.
	"""
	for fieldname in ("custom_walkin_tax_id", "tax_id"):
		value = (invoice.get(fieldname) or "").strip()
		if value:
			return value
	return None
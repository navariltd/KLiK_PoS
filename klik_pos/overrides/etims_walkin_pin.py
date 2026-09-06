"""Get the walk-in checkout's Tax ID/PIN to eTIMS by reflecting it onto the
shared walk-in Customer record for the exact duration of doc.submit(),
instead of monkeypatching the Kenya Compliance (eTIMS) app's internals.

WHY THIS REPLACES THE EARLIER MONKEYPATCH APPROACH:

The previous version of this file patched
kenya_compliance_via_slade.kenya_compliance_via_slade.utils.build_invoice_payload
at process-import time. That turned out to have two problems in practice:

1. It never actually reached production. klik_pos/__init__.py imported this
   module from a try/except that only prints on failure, so when the file
   went missing from this bench's deployed commit (a branch/deploy mismatch,
   not a bug in the patch itself), the whole override silently no-op'd with
   no trace in the logs -- exactly the failure mode that made this hard to
   diagnose the first time.
2. Even once deployed, it depends on kenya_compliance_via_slade's *internal*,
   undocumented module layout (exact function name, every module that
   imports it by name) staying stable release to release. A future update to
   that app could rename or restructure build_invoice_payload and silently
   break this again, the same invisible way.

This version avoids both problems by never touching the compliance app's
code at all. Root cause, unchanged from before:

- kenya_compliance_via_slade's build_invoice_payload sets "customer_pin"
  from `frappe.get_value("Customer", invoice.customer, "tax_id")` -- a
  fresh database read of the CUSTOMER record, not the Sales Invoice.
- Sales Invoice's own validate()/set_missing_values(), which reruns as
  part of doc.submit(), pulls tax_id FROM that same Customer record too --
  which is why the walk-in PIN was getting silently blanked out on the
  invoice itself, not just missing from the eTIMS payload.

Both of those read from Customer.tax_id, which for the shared walk-in
"Cash Customer" record is permanently blank. So: for the exact duration of
doc.submit(), this module temporarily writes the checkout's captured PIN
onto that Customer record, lets kenya_compliance_via_slade (and Sales
Invoice's own validate()) read it the completely normal, unpatched way,
then restores the Customer record's original value immediately after --
success or failure -- so the next walk-in sale (a different buyer, a
different PIN, or none at all) never inherits a stale PIN left over from
someone else's transaction.

CONCURRENCY: "Cash Customer" (or any Customer flagged custom_is_walkin) is
a SHARED record across every walk-in sale. Reflecting a PIN onto it and
restoring it afterward is only safe if no other walk-in submission is
reading or writing that same Customer row at the same moment -- two
overlapping submissions could otherwise cross PINs between two different
buyers' invoices, which is a worse outcome than a missing PIN: a PIN
attributed to the wrong buyer on an actual KRA filing.

reflect_walkin_pin_on_customer() takes a `SELECT ... FOR UPDATE` row lock
on the Customer record before writing, and holds it until the value is
restored. Any other request touching that same Customer row -- a second
till, a background-queued invoice racing an immediate one, a double-submit
-- blocks until the first one releases the lock, rather than reading a
value mid-flight.

That said, the lock is a real serialization point, and it is NOT an
absolute guarantee: if kenya_compliance_via_slade's own on_submit handling
calls frappe.db.commit() partway through (some integrations do, to persist
"already sent to the tax authority" state before an external HTTP call, so
a retry doesn't double-submit), a row lock taken in the same transaction
is released at that commit -- narrowing the unsafe window rather than
closing it completely for that specific case. For a single person driving
one till (confirmed as this pharmacy's current setup), two walk-in
submissions can't physically overlap in the first place, so this doesn't
matter today. It would need a fresh look -- most likely moving off a
single shared "Cash Customer" record entirely -- before adding a second
concurrent till.
"""

import contextlib

import frappe


@contextlib.contextmanager
def reflect_walkin_pin_on_customer(customer, tax_id):
	"""Use as:

		with reflect_walkin_pin_on_customer(doc.customer, tax_id):
			doc.submit()

	No-ops (no lock, no writes) when tax_id is blank or `customer` isn't
	flagged as walk-in -- a normal registered customer's Customer.tax_id is
	already the real, persistent value on file and must never be touched.
	"""
	tax_id = (tax_id or "").strip()
	if not tax_id or not _is_walkin(customer):
		yield
		return

	# for_update=True takes the row lock and reads the current value in one
	# round trip -- see the CONCURRENCY note above for what this does and
	# does not protect against.
	original_tax_id = frappe.db.get_value("Customer", customer, "tax_id", for_update=True)

	applied = False
	try:
		frappe.db.set_value("Customer", customer, "tax_id", tax_id, update_modified=False)
		applied = True
		frappe.logger("klik_pos").info(
			f"eTIMS walk-in PIN: reflected onto Customer {customer} for this submit (...{tax_id[-3:]})"
		)
		yield
	finally:
		if applied:
			frappe.db.set_value("Customer", customer, "tax_id", original_tax_id, update_modified=False)
			frappe.logger("klik_pos").info(
				f"eTIMS walk-in PIN: restored Customer {customer}.tax_id to its prior value"
			)


def _is_walkin(customer):
	if not customer:
		return False
	try:
		return frappe.utils.cint(frappe.db.get_value("Customer", customer, "custom_is_walkin") or 0) == 1
	except Exception:
		return False
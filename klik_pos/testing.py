"""Hand the site back as it was after a test run.

ERPNext's test bootstrap (erpnext/tests/utils.py, BootStrapTestData) runs on every
import and commits Asia/Kolkata, Banker's Rounding, Standard Selling and its stock
defaults into the site. On a working site that sends tills to Closing Shift (the
shift looks like yesterday's). This snapshots those settings before the run and
writes them back when the process exits, pass or fail.
"""

import atexit
import time

import frappe

# What BootStrapTestData.update_*_settings() changes.
SETTINGS = {
	"System Settings": ("time_zone", "language", "currency_precision", "float_precision", "rounding_method"),
	"Selling Settings": ("selling_price_list",),
	"Support Settings": ("track_service_level_agreement",),
	"Stock Settings": (
		"item_naming_by",
		"valuation_method",
		"stock_uom",
		"auto_indent",
		"auto_insert_price_list_rate_if_missing",
		"update_price_list_based_on",
		"set_qty_in_transactions_based_on_serial_no_input",
		"enable_serial_and_batch_no_for_item",
	),
}


def snapshot():
	rows = frappe.db.sql(
		"select doctype, field, value from tabSingles where doctype in %(dts)s",
		{"dts": tuple(SETTINGS)},
		as_dict=True,
	)
	return {(r.doctype, r.field): r.value for r in rows if r.field in SETTINGS[r.doctype]}


def restore(saved, site=None):
	if site and not getattr(frappe.local, "db", None):
		frappe.init(site=site)
		frappe.connect()
	# Another session's test run on the same site can deadlock this write: retry a few times.
	for attempt in range(5):
		try:
			for (doctype, field), value in saved.items():
				frappe.db.set_single_value(doctype, field, value)
			frappe.db.commit()  # nosemgrep: the run is over, nothing to roll back into
			break
		except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
			frappe.db.rollback()
			if attempt == 4:
				raise
			time.sleep(2)
	frappe.clear_cache()


def keep_site_settings():
	"""Called from klik_pos/tests/__init__.py (imported once per run): it runs before any test module imports
	erpnext.tests.utils, so the snapshot holds the site's own values. (Frappe's before_tests
	hook comes after discovery has imported the modules, so it would snapshot the test values.)"""
	atexit.register(restore, snapshot(), frappe.local.site)


# The records klik_pos tests were written against on dev.localhost. A clean test site has
# none of them, so the suite makes any that are missing, once, before the first test.
COMPANY = "Dev Co"


def ensure_dev_records():
	"""Dev Co with its till, Walk In and M-Pesa mode, made only where missing (never altered)."""
	if not frappe.db.exists("Company", COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": COMPANY,
				"abbr": "DC",
				"default_currency": "KES",
				"country": "Kenya",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard with Numbers",
			}
		).insert(ignore_permissions=True)

	_account("VAT", "2300 - Duties and Taxes - DC", account_type="Tax", tax_rate=16)
	_account("Mpesa-111222", "1200 - Bank Accounts - DC", account_type="Bank")
	# As on dev, the cash account carries several modes, so its mode cannot be guessed.
	for mode in ("Cash", "Cheque", "Credit Card", "Bank Draft"):
		_mode_account(mode, "1110 - Cash - DC")
	if not frappe.db.exists("Mode of Payment", "Mpesa-111222"):
		frappe.get_doc(
			{"doctype": "Mode of Payment", "mode_of_payment": "Mpesa-111222", "type": "Phone", "enabled": 1}
		).insert(ignore_permissions=True)
	_mode_account("Mpesa-111222", "Mpesa-111222 - DC")

	# KES, as on dev: ERPNext's test bootstrap leaves Standard Selling in INR.
	if not frappe.db.exists("Price List", "Retail"):
		frappe.get_doc(
			{"doctype": "Price List", "price_list_name": "Retail", "currency": "KES", "selling": 1, "enabled": 1}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Customer", "Walk In"):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "Walk In",
				"customer_type": "Individual",
				"default_currency": "KES",
				"default_price_list": "Retail",
				"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}),
				"territory": frappe.db.get_value("Territory", {"is_group": 0}),
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("POS Profile", "_Test POS Profile"):
		profile = frappe.get_doc(
			{
				"doctype": "POS Profile",
				"__newname": "_Test POS Profile",
				"company": COMPANY,
				"customer": "Walk In",
				"currency": "KES",
				"warehouse": "Stores - DC",
				"selling_price_list": "Retail",
				"write_off_account": "4110 - Sales - DC",
				"write_off_cost_center": "Main - DC",
				"cost_center": "Main - DC",
				"write_off_limit": 1,
				"update_stock": 1,
				"allow_rate_change": 1,
				"allow_discount_change": 1,
				"is_tax_included_in_basic_rate": 1,
				"payments": [
					{"mode_of_payment": "Cash", "default": 1},
					{"mode_of_payment": "Mpesa-111222"},
				],
				"applicable_for_users": [{"user": "Administrator", "default": 1}],
				"custom_allow_return": 1,
				"custom_allow_closing_shift": 1,
				"custom_allow_viewing_other_cashiers": 1,
				"custom_allow_quotes": 1,
				"custom_default_view": "List View",
			}
		)
		profile.insert(ignore_permissions=True, set_name="_Test POS Profile")

	# A new document starts in the site's default company and currency: dev's are Dev Co
	# and KES, a fresh site's none and INR, and Dev Co's receivables refuse INR.
	for key, value in (("company", COMPANY), ("currency", "KES")):
		if frappe.db.get_default(key) != value:
			frappe.db.set_default(key, value)
	frappe.db.set_single_value("Global Defaults", {"default_company": COMPANY, "default_currency": "KES"})
	# Also as on dev: the same item may sit on two lines, and an address has a country.
	frappe.db.set_single_value("Selling Settings", "allow_multiple_items", 1)
	if not frappe.db.get_default("country"):
		frappe.db.set_default("country", "Kenya")

	frappe.db.commit()  # nosemgrep: fixtures outlive the run, like ERPNext's own test records


def _account(name, parent, **fields):
	if frappe.db.exists("Account", f"{name} - DC"):
		return
	frappe.get_doc(
		{"doctype": "Account", "account_name": name, "parent_account": parent, "company": COMPANY, **fields}
	).insert(ignore_permissions=True)


def _mode_account(mode, account):
	doc = frappe.get_doc("Mode of Payment", mode)
	if any(row.company == COMPANY for row in doc.accounts):
		return
	doc.append("accounts", {"company": COMPANY, "default_account": account})
	doc.save(ignore_permissions=True)


def foreign_sales_order_workflows_off():
	"""Deactivate active Sales Order workflows other than PowerPack's price approval; return them.

	PowerPack refuses to turn price approval on beside another active workflow, and on a fresh
	site cecypo_field_sales installs its Field Order Approval first. Hand the list back to
	`workflows_on` when the class is done.
	"""
	names = frappe.get_all(
		"Workflow",
		{"document_type": "Sales Order", "is_active": 1, "name": ["not like", "PowerPack Price Approval%"]},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value("Workflow", name, "is_active", 0)
	frappe.clear_cache(doctype="Sales Order")
	return names


def workflows_on(names):
	for name in names or ():
		frappe.db.set_value("Workflow", name, "is_active", 1)
	frappe.clear_cache(doctype="Sales Order")
	frappe.db.commit()  # nosemgrep: undoes a committed change; runs as a class cleanup


def foreign_sales_order_workflows_off_for(cls):
	"""foreign_sales_order_workflows_off for a test class, restored by a class cleanup: that runs
	even when setUpClass skips or fails after this, where tearDownClass would not."""
	names = foreign_sales_order_workflows_off()
	frappe.db.commit()  # nosemgrep: PowerPack's settings check reads it in another save
	cls.addClassCleanup(workflows_on, names)


def purge_voucher(doctype, name):
	"""Remove a test's committed voucher and every ledger row and request that names it.

	Cancelling leaves the GL, payment and stock ledger rows, and checkout requests outlive
	the invoice; names are reused once a series rolls back, so a later test's draft would
	inherit those links and could no longer be discarded.
	"""
	if not frappe.db.exists(doctype, name):
		return
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()
	for ledger in ("GL Entry", "Payment Ledger Entry", "Stock Ledger Entry"):
		frappe.db.delete(ledger, {"voucher_type": doctype, "voucher_no": name})
	if doctype == "Sales Invoice":
		frappe.db.delete("Klik Checkout Request", {"sales_invoice": name})
	frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def any_till_currency(cls):
	"""Switch off the till-currency filter for a test class about other price-list rules: its lists
	are ERPNext's INR test lists, while the test user's till sells in KES."""
	from unittest.mock import patch

	for target in (
		"klik_pos.api.item.item_price.till_currency",
		"klik_pos.api.item.pricing.till_currency",
		"klik_pos.api.customer.till_currency",
	):
		patcher = patch(target, return_value=None)
		patcher.start()
		cls.addClassCleanup(patcher.stop)

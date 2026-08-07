"""Statement of Accounts, delegated to cecypo_frappe_reports.

That app already owns a complete, whitelisted Statement of Accounts backend built on ERPNext's
Process Statement Of Accounts, including the permission checks, the in-memory PSOA clone and the
branded render path. klik_pos forwards to it rather than carrying a copy, so a fix made there
appears here with no change on this side.

The dependency is soft: klik_pos installs and runs without cecypo_frappe_reports, and the
frontend asks is_available() so it can hide the feature rather than offer one that errors.
"""

import frappe
from frappe import _

UPSTREAM_APP = "cecypo_frappe_reports"
UPSTREAM_MODULE = "cecypo_frappe_reports.cecypo_frappe_reports.statement_of_accounts"


def _upstream(method):
	"""Resolve an upstream callable. Raises ImportError/AttributeError when unavailable."""
	return frappe.get_attr(f"{UPSTREAM_MODULE}.{method}")


def _delegate(method, **kwargs):
	"""Forward a call upstream, translating a missing app into a readable message.

	kwargs are passed through untouched rather than re-declared, so a parameter added upstream
	flows through without a change here.
	"""
	try:
		fn = _upstream(method)
	except (ImportError, AttributeError, frappe.AppNotInstalledError):
		frappe.throw(
			_("Statements need the {0} app, which is not installed on this site.").format(UPSTREAM_APP)
		)

	# Deliberately NOT wrapped in try/except. Upstream throws carry the reason a user needs —
	# "No transactions for X in the selected period", "template belongs to another company" —
	# and the usual {"success": False} envelope would flatten them into a generic failure.
	return fn(**kwargs)


@frappe.whitelist()
def is_available():
	"""Whether the statement feature can be used BY THIS USER on this site.

	Never raises: the frontend calls this to decide whether to render a button at all.

	Resolution is not capability. The upstream endpoints gate on Process Statement Of
	Accounts read permission, and a site with a Custom DocPerm on that doctype can grant it
	to a small minority of users. Checking only that the module imports would show the button
	to everyone and open it onto a permission error.
	"""
	try:
		_upstream("render_statement_html")
	except Exception:
		return {"available": False}

	if not frappe.has_permission("Process Statement Of Accounts", "read"):
		return {"available": False}

	return {"available": True}


@frappe.whitelist()
def get_statement_templates(company):
	return _delegate("get_statement_templates", company=company)


@frappe.whitelist()
def get_default_recipient(party_type, party):
	return _delegate("get_default_recipient", party_type=party_type, party=party)


@frappe.whitelist()
def render_statement_html(customer, company, template, as_of_date=None):
	return _delegate(
		"render_statement_html",
		customer=customer,
		company=company,
		template=template,
		as_of_date=as_of_date,
	)


@frappe.whitelist()
def download_statement(customer, company, template, as_of_date=None):
	# Upstream sets frappe.local.response (filename/filecontent/type="download"). That is
	# request-scoped, so setting it from inside a delegated call works exactly as if the
	# upstream endpoint had been called directly.
	return _delegate(
		"download_statement",
		customer=customer,
		company=company,
		template=template,
		as_of_date=as_of_date,
	)


@frappe.whitelist()
def email_statement(customer, company, template, as_of_date=None, recipient=None, cc="", bcc=""):
	return _delegate(
		"email_statement",
		customer=customer,
		company=company,
		template=template,
		as_of_date=as_of_date,
		recipient=recipient,
		cc=cc,
		bcc=bcc,
	)

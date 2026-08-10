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
UPSTREAM_APP_LABEL = "Cecypo Frappe Reports"
UPSTREAM_MODULE = "cecypo_frappe_reports.cecypo_frappe_reports.statement_of_accounts"

# The upstream contract this app is written against. See STATEMENT_API_VERSION upstream.
REQUIRED_UPSTREAM_API = 2

# Every dotted path this module delegates to. Resolving one of them proves the module imports;
# resolving all of them proves the version installed actually offers what we call.
REQUIRED_UPSTREAM_METHODS = (
	"get_statement_templates",
	"get_default_recipient",
	"render_statement_html",
	"download_statement",
	"email_statement",
	"preview_bulk_statements",
	"email_bulk_statements",
)


def _upstream(method):
	"""Resolve an upstream callable. Raises ImportError/AttributeError when unavailable."""
	return frappe.get_attr(f"{UPSTREAM_MODULE}.{method}")


def _upstream_api_version():
	"""The contract version upstream declares, or 1 if it declares none.

	An upstream predating the constant offered the pre-manifest shape, which is version 1 by
	definition. Treating absence as 1 rather than as an error means an old app degrades to
	"too old" instead of "broken", which is the direction that fails safe.
	"""
	try:
		return int(_upstream("STATEMENT_API_VERSION"))
	except Exception:
		return 1


def _upstream_capability():
	"""(ok, reason). Never raises — callers use it to decide whether to offer a feature."""
	try:
		for method in REQUIRED_UPSTREAM_METHODS:
			_upstream(method)
	except (ImportError, AttributeError, frappe.AppNotInstalledError) as exc:
		return False, _("{0} is not installed, or is missing {1}.").format(
			UPSTREAM_APP_LABEL, exc
		)
	except Exception as exc:
		return False, str(exc)

	found = _upstream_api_version()
	if found < REQUIRED_UPSTREAM_API:
		return False, _(
			"{0} provides statement API version {1}, but {2} is required. Update that app."
		).format(UPSTREAM_APP_LABEL, found, REQUIRED_UPSTREAM_API)

	return True, None


def _delegate(method, **kwargs):
	"""Forward a call upstream, refusing rather than half-working against a stale contract.

	kwargs are passed through untouched rather than re-declared, so a parameter added upstream
	flows through without a change here.
	"""
	ok, reason = _upstream_capability()
	if not ok:
		# Refuse at the endpoint, not only in the UI. is_available hides a button; a direct API
		# call or a stale SPA bundle never consults it, and half-working against an old
		# contract is worse than a clear refusal.
		frappe.throw(reason)

	# Deliberately NOT wrapped in try/except. Upstream throws carry the reason a user needs —
	# "No transactions for X in the selected period", "template belongs to another company" —
	# and the usual {"success": False} envelope would flatten them into a generic failure.
	return _upstream(method)(**kwargs)


@frappe.whitelist()
def is_available():
	"""Whether the statement feature can be used BY THIS USER on this site.

	Never raises: the frontend calls this to decide whether to render a button at all. Returns
	a reason alongside the verdict so an operator can tell "not installed" from "too old" from
	"you lack permission" without reading code.
	"""
	try:
		ok, reason = _upstream_capability()
		if not ok:
			# Logged, not thrown: a cashier cannot act on this, but whoever is wondering why
			# the button vanished needs it to be findable.
			frappe.logger("klik_pos").warning(f"Statement feature unavailable: {reason}")
			return {"available": False, "reason": reason}

		# Resolution is not capability. The upstream endpoints gate on Process Statement Of
		# Accounts read permission, and a site with a Custom DocPerm on that doctype can grant
		# it to a small minority. Inside the try because has_permission calls get_meta, which
		# raises if the doctype is absent — and this function must always answer.
		if not frappe.has_permission("Process Statement Of Accounts", "read"):
			return {
				"available": False,
				"reason": _("You do not have permission to read Process Statement Of Accounts."),
			}
	except Exception as exc:
		return {"available": False, "reason": str(exc)}

	return {"available": True, "reason": None}


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
def preview_bulk_statements(company, template, as_of_date=None):
	return _delegate(
		"preview_bulk_statements", company=company, template=template, as_of_date=as_of_date
	)


@frappe.whitelist(methods=["POST"])
def email_bulk_statements(company, template, as_of_date=None):
	# POST only: @frappe.whitelist() with no methods accepts GET, and Frappe only validates CSRF
	# for unsafe methods — an <img src="...?company=X&template=Y"> on any page a logged-in user
	# visits would otherwise fire a customer-wide mail blast through this delegating endpoint.
	# The SPA service already calls this via POST (see statementOfAccounts.ts), so this changes
	# nothing for the real caller.
	return _delegate(
		"email_bulk_statements", company=company, template=template, as_of_date=as_of_date
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

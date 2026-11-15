from urllib.parse import unquote

import frappe
from frappe import _
from frappe.utils import cint

_cached_company_data = {}


def _till_cache():
	"""The till each f"{user}|{shift or 'none'}" resolves to - for this request only.

	It was process-wide: each web worker kept its own, so a deletion handled by one worker
	left the others resolving the reused shift name to the deleted till.
	"""
	if not hasattr(frappe.local, "klik_till_cache"):
		frappe.local.klik_till_cache = {}
	return frappe.local.klik_till_cache


def error_reason(error):
	"""What went wrong, for the cashier. A bare PermissionError has no message of its own, so
	"Failed to create POS Opening Entry: {0}" used to end in nothing."""
	if isinstance(error, frappe.QueryTimeoutError | frappe.QueryDeadlockError):
		# A raw "(1205, 'Lock wait timeout exceeded...')" means nothing at the counter.
		return _("The system is busy with another update. Please try again in a moment.")
	if str(error):
		return str(error)
	if isinstance(error, frappe.PermissionError):
		return _("you do not have permission for this. Ask your manager to check your role.")
	return error.__class__.__name__


# The till's picked warehouse, sent with every request (klik_spa/src/utils/warehouseHeader.ts).
WAREHOUSE_HEADER = "X-Klik-Warehouse"


def checked_warehouse(profile, wanted):
	"""`wanted` if this till may sell from it, None for the till's own; throws otherwise."""
	wanted = (wanted or "").strip()
	if not wanted or wanted == profile.get("warehouse"):
		return None
	if not cint(profile.get("allow_warehouse_change")):
		frappe.throw(_("Till {0} does not allow switching warehouse.").format(profile.get("name")))
	from klik_pos.api.pos_profile import leaf_warehouses_for

	if wanted not in leaf_warehouses_for(profile.get("company"), frappe.session.user):
		frappe.throw(_("You cannot sell from warehouse {0} at this till.").format(wanted))
	return wanted


def requested_warehouse(profile):
	"""The warehouse the till picked for this request, checked; None for the till's own."""
	request = getattr(frappe.local, "request", None)
	headers = getattr(request, "headers", None)
	# URL-encoded by the till: a header value is Latin-1 only.
	return checked_warehouse(profile, unquote(headers.get(WAREHOUSE_HEADER) or "") if headers else None)


def _resolve_pos_profile_name():
	"""Resolve the active POS Profile name for the current user/opening entry.

	Uses identity-only caching keyed by user and opening entry (the name is
	stable for a session; only the profile's field values may change).
	"""
	user = frappe.session.user

	from klik_pos.api.sales_invoice import get_current_pos_opening_entry

	current_opening_entry = get_current_pos_opening_entry()
	cache_key = f"{user}|{current_opening_entry or 'none'}"

	cache = _till_cache()
	if cache_key in cache:
		return cache[cache_key]

	if current_opening_entry:
		pos_profile_name = frappe.db.get_value("POS Opening Entry", current_opening_entry, "pos_profile")
	else:
		pos_profile_name = frappe.get_value("POS Profile User", {"user": user}, "parent")
		if not pos_profile_name:
			frappe.throw(_("No POS Profile found for user {0}").format(user))

	# Cache identity (name) only
	cache[cache_key] = pos_profile_name
	# A rolled-back shift's name is handed out again; its till must not answer for the next one.
	frappe.db.after_rollback.add(lambda: cache.pop(cache_key, None))
	return pos_profile_name


def get_current_pos_profile():
	"""Get the active POS Profile with identity-only caching keyed by user and opening entry.

	Returns a fresh POS Profile Doc each call to avoid stale field values.
	"""
	pos_profile_name = _resolve_pos_profile_name()

	# Mania: Always fetch a fresh doc to ensure latest fields -> Issue reported 04/11/2025
	pos_profile_doc = frappe.get_doc("POS Profile", pos_profile_name)
	pos_profile_doc.warehouse = requested_warehouse(pos_profile_doc) or pos_profile_doc.warehouse
	return pos_profile_doc


def till_currency():
	"""The currency the current till sells in: its first mode of payment's account currency, else
	the till's own currency, else the company's. None off a till (desk, scripts): no filter then."""
	try:
		name = _resolve_pos_profile_name()
	except frappe.ValidationError:
		frappe.clear_last_message()
		return None
	profile = frappe.db.get_value("POS Profile", name, ["company", "currency"], as_dict=True)
	if not profile:
		return None
	mode = frappe.db.get_value(
		"POS Payment Method", {"parent": name, "parenttype": "POS Profile"}, "mode_of_payment", order_by="idx asc"
	)
	account = mode and frappe.db.get_value(
		"Mode of Payment Account", {"parent": mode, "company": profile.company}, "default_account"
	)
	return (
		(account and frappe.get_cached_value("Account", account, "account_currency"))
		or profile.currency
		or frappe.get_cached_value("Company", profile.company, "default_currency")
	)


def get_current_pos_profile_lite(fields):
	"""Return only the requested POS Profile fields as a frappe._dict.

	Avoids loading the full document (parent row + every child table: taxes,
	item_groups, payments, …) on latency-critical paths that need just a few
	scalar fields, e.g. barcode/identifier scan lookups. Values are read live
	from the DB, so field freshness matches get_current_pos_profile.
	"""
	pos_profile_name = _resolve_pos_profile_name()
	extra = ["name", "warehouse", "company", "allow_warehouse_change"] if "warehouse" in fields else []
	values = frappe.db.get_value(
		"POS Profile", pos_profile_name, list(dict.fromkeys([*fields, *extra])), as_dict=True
	)
	values = values or frappe._dict()
	if "warehouse" in fields and values:
		values.warehouse = requested_warehouse(values) or values.warehouse
	return values


def forget_deleted_shift(doc, method=None):
	"""after_delete on POS Opening Entry: the series steps back, so the next shift takes this
	name - and must not resolve to the till this one was on."""
	cache = _till_cache()
	for key in [k for k in cache if k.endswith(f"|{doc.name}")]:
		cache.pop(key, None)


def clear_pos_profile_cache(user=None):
	"""Forget the tills cached for `user` (default: the session user)."""
	cache = _till_cache()
	prefix = f"{user or frappe.session.user}|"
	for key in [k for k in cache if k.startswith(prefix)]:
		del cache[key]


def get_user_default_company():
	"""The till's company, else the user's default one, else their default Company permission.

	frappe.defaults.get_user_default("Company") alone is not enough: a desk Session Default is
	cleared on logout, and Frappe then offers the global default company - which it drops
	again when the user is restricted to another company, returning None.
	"""
	try:
		company = frappe.db.get_value("POS Profile", _resolve_pos_profile_name(), "company")
	except Exception:
		# frappe.throw queued its message for the client; no till is not an error here.
		frappe.clear_last_message()
		company = None
	if company:
		return company
	if company := frappe.defaults.get_user_default("Company"):
		return company
	from frappe.permissions import get_user_permissions

	perms = get_user_permissions(frappe.session.user).get("Company") or []
	return next((p.doc for p in perms if p.is_default), perms[0].doc if len(perms) == 1 else None)

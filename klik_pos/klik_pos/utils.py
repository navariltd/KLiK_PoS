import frappe
from frappe import _

# Performance optimization: Cache frequently accessed data per user and opening entry
# Key: f"{user}|{opening_entry or 'none'}", Value: POS Profile name (identity only)
_cached_pos_profiles = {}
_cached_company_data = {}


def _resolve_pos_profile_name():
	"""Resolve the active POS Profile name for the current user/opening entry.

	Uses identity-only caching keyed by user and opening entry (the name is
	stable for a session; only the profile's field values may change).
	"""
	user = frappe.session.user

	from klik_pos.api.sales_invoice import get_current_pos_opening_entry

	current_opening_entry = get_current_pos_opening_entry()
	cache_key = f"{user}|{current_opening_entry or 'none'}"

	if cache_key in _cached_pos_profiles:
		return _cached_pos_profiles[cache_key]

	if current_opening_entry:
		pos_profile_name = frappe.db.get_value("POS Opening Entry", current_opening_entry, "pos_profile")
	else:
		pos_profile_name = frappe.get_value("POS Profile User", {"user": user}, "parent")
		if not pos_profile_name:
			frappe.throw(_("No POS Profile found for user {0}").format(user))

	# Cache identity (name) only
	_cached_pos_profiles[cache_key] = pos_profile_name
	return pos_profile_name


def get_current_pos_profile():
	"""Get the active POS Profile with identity-only caching keyed by user and opening entry.

	Returns a fresh POS Profile Doc each call to avoid stale field values.
	"""
	pos_profile_name = _resolve_pos_profile_name()

	# Mania: Always fetch a fresh doc to ensure latest fields -> Issue reported 04/11/2025
	pos_profile_doc = frappe.get_doc("POS Profile", pos_profile_name)
	return pos_profile_doc


def get_current_pos_profile_lite(fields):
	"""Return only the requested POS Profile fields as a frappe._dict.

	Avoids loading the full document (parent row + every child table: taxes,
	item_groups, payments, …) on latency-critical paths that need just a few
	scalar fields, e.g. barcode/identifier scan lookups. Values are read live
	from the DB, so field freshness matches get_current_pos_profile.
	"""
	pos_profile_name = _resolve_pos_profile_name()
	values = frappe.db.get_value("POS Profile", pos_profile_name, fields, as_dict=True)
	return values or frappe._dict()


def clear_pos_profile_cache(user=None):
	"""Clear cached POS Profile identities. If user provided, clear all entries for that user."""
	global _cached_pos_profiles

	if user:
		# Clear all cache entries matching the user prefix
		keys_to_delete = [k for k in list(_cached_pos_profiles.keys()) if k.startswith(f"{user}|")]
		for k in keys_to_delete:
			del _cached_pos_profiles[k]
		if keys_to_delete:
			frappe.logger().info(
				f"🧹 POS Profile cache cleared for user: {user} ({len(keys_to_delete)} entries)"
			)
	else:
		# Clear cache for current user
		current_user = frappe.session.user
		keys_to_delete = [k for k in list(_cached_pos_profiles.keys()) if k.startswith(f"{current_user}|")]
		for k in keys_to_delete:
			del _cached_pos_profiles[k]
		if keys_to_delete:
			frappe.logger().info(
				f"🧹 POS Profile cache cleared for user: {current_user} ({len(keys_to_delete)} entries)"
			)


def get_user_default_company():
	user = frappe.session.user
	return frappe.defaults.get_user_default(user, "Company")

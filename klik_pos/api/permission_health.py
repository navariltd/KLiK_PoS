"""Tell the cashier what their role cannot see, before it costs them a sale.

klik_pos degrades rather than erroring when a doctype is unreadable: apply_sql_permissions
neuters the query so one missing permission cannot take the whole POS down. That is the right
trade, but it means a permission gap arrives as a plausible-looking wrong number - zero stock,
a missing price - with nothing saying why.

get_items reports its own degradation in the response. This module covers the rest: rather
than wiring a degraded flag into thirteen endpoints, most of which render into views with
nowhere to show it, check the dependencies once at startup. Deterministic, because it does not
depend on which query happened to run, and it warns before a wrong number is acted on instead
of after.
"""

import frappe
from frappe import _

CRITICAL = "critical"
DEGRADED = "degraded"

# What the POS actually depends on, and what breaks without it. Ordered most-severe first so
# the caller can render them in the order they matter.
#
# Consequences are stored untranslated and passed through _() at call time: a module-level
# _() would be evaluated once at import, freezing every user's message into whichever language
# the first request happened to use.
POS_PERMISSION_REQUIREMENTS = [
	("Item", "read", CRITICAL, "The product catalogue cannot load."),
	("POS Profile", "read", CRITICAL, "The POS cannot resolve its configuration."),
	("Sales Invoice", "create", CRITICAL, "Sales cannot be recorded."),
	(
		"Bin",
		"read",
		DEGRADED,
		"Stock levels are unknown, so availability shows as unknown and out-of-stock "
		"filtering is switched off.",
	),
	("Item Price", "read", DEGRADED, "Prices may be missing or show as zero."),
	("Item Group", "read", DEGRADED, "Category filtering may be incomplete."),
	("Warehouse", "read", DEGRADED, "The POS may not resolve a default warehouse."),
	("Customer", "read", DEGRADED, "Customer selection and credit sales may be unavailable."),
	("Mode of Payment", "read", DEGRADED, "Some payment methods may be missing."),
]

# A doctype with any Custom DocPerm has its standard DocPerms discarded wholesale - Frappe
# replaces rather than merges (see permissions.get_valid_perms). Resolving granting roles has
# to respect that, or it would name roles that in fact grant nothing.
_PERM_DOCTYPES = ("Custom DocPerm", "DocPerm")

# Enough to act on without turning the banner into a wall of role names.
_MAX_GRANTING_ROLES = 4


def _granting_roles(doctype, ptype):
	"""Roles that would actually grant `ptype` on `doctype` on THIS site.

	Naming a real role is the difference between an actionable message and a misleading one:
	which role carries a permission varies per site, so guessing at "the Stock User role" is
	worse than useless where that is not what grants it.
	"""
	try:
		source = "Custom DocPerm" if frappe.db.exists("Custom DocPerm", {"parent": doctype}) else "DocPerm"
		roles = frappe.get_all(
			source,
			filters={"parent": doctype, "permlevel": 0, ptype: 1},
			pluck="role",
			ignore_permissions=True,
		)
	except Exception:
		return []

	# Administrator holds everything, so offering it as a remedy is noise.
	roles = sorted({role for role in roles if role and role != "Administrator"})
	return roles[:_MAX_GRANTING_ROLES]


@frappe.whitelist()
def get_permission_health():
	"""Report POS dependencies the current user cannot reach.

	Never raises: a health check that can break the POS is worse than no health check.
	"""
	missing = []

	for doctype, ptype, severity, consequence in POS_PERMISSION_REQUIREMENTS:
		try:
			if not frappe.db.exists("DocType", doctype):
				# Shipped by an app this site does not have; not a permission problem.
				continue
			if frappe.has_permission(doctype, ptype):
				continue
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"POS permission health check failed for {doctype}")
			continue

		missing.append(
			{
				"doctype": doctype,
				"permission": ptype,
				"severity": severity,
				"consequence": _(consequence),
				"granting_roles": _granting_roles(doctype, ptype),
			}
		)

	return {
		"healthy": not missing,
		"missing": missing,
		"has_critical": any(entry["severity"] == CRITICAL for entry in missing),
	}

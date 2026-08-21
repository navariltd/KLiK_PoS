"""Single source of truth for the role sets klik_pos branches on.

These lived inline at six call sites and had drifted into five different spellings of
the same idea - the frontend enabled a cashier dropdown for Sales Manager that the
backend query then refused to fill. One list, imported everywhere, is the fix.

Nothing here looks a role up. Each check is a string comparison against the caller's
own roles, so a klik_pos install without erpnext_express simply never matches the
Express name and behaves exactly as it did before it was added.
"""

import frappe

# Roles that see every till: other cashiers' invoices, the whole day's payment totals,
# anyone's held orders, every unposted-queue failure. Adding a role here grants
# visibility over other people's takings - it is not a cosmetic list.
#
# Express Admin belongs here because it is Express's back-office operator: full CRUD on
# Sales Invoice, Payment Entry, Journal Entry and POS Profile. Express Sales, Stocks and
# Purchase deliberately do NOT - a till user sees their own documents only. Express's
# role hygiene hook forbids mixing an Express role with a standard one, so naming the
# role here is the only way an Express deployment can have a manager at all.
ADMIN_ROLES = ["Administrator", "Sales Manager", "System Manager", "Express Admin"]

# Roles permitted to open the Sales Dashboard. Currently the same set as ADMIN_ROLES -
# every role that may read the dashboard may also read the data behind it. Kept as its
# own name because the dashboard is a UI gate and ADMIN_ROLES is a data-scope rule; a
# role that should read one without the other would diverge here, not in ADMIN_ROLES.
DASHBOARD_ROLES = list(ADMIN_ROLES)

# Who gets told about a failed queued invoice, in addition to the cashier who rang it.
# Not ADMIN_ROLES: this one is resolved through a `Has Role` query, and "Administrator"
# there means the literal Administrator account rather than "anyone with full rights",
# so including it would start mailing a system login that reads no POS alerts.
ALERT_ROLES = ["Sales Manager", "System Manager", "Express Admin"]


def _has_any(roles, user=None):
	return bool(set(roles) & set(frappe.get_roles(user or frappe.session.user)))


def is_admin_user(user=None):
	"""True when the user may see other people's tills. See ADMIN_ROLES."""
	return _has_any(ADMIN_ROLES, user)


def can_view_sales_dashboard(user=None):
	"""True when the user may open the Sales Dashboard. See DASHBOARD_ROLES."""
	return _has_any(DASHBOARD_ROLES, user)

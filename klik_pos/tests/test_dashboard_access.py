"""Opening the Sales Dashboard and seeing every till's data are two different rights.

`is_admin_user` was doing both jobs: it gated the Dashboard tab in the three nav
components AND widened the cashier filter and POS-profile scope in DashboardPage and
InvoiceHistory. So the only way to let a role read the dashboard was to also hand it
visibility over every cashier - and on an ERPNext Express site there is no way at all,
because Express roles hold none of the three names in `admin_roles` and Express's role
hygiene hook (erpnext_express/user_hooks.py) refuses to let an Express user carry a
standard role alongside.

`can_view_sales_dashboard` splits the tab gate off so it can name Express Admin without
touching data scope. `is_admin_user` keeps its exact former meaning and membership -
these tests pin that, because widening it silently is the failure this split exists to
prevent.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.user import ADMIN_ROLES, DASHBOARD_ROLES, get_current_user_info, get_user_roles


def _with_roles(*roles):
	"""Run the endpoint as a user holding exactly these roles."""
	return patch("frappe.get_roles", return_value=list(roles))


class TestSalesDashboardAccess(FrappeTestCase):
	def _payload(self, *roles):
		with _with_roles(*roles):
			result = get_user_roles()
		self.assertTrue(result["success"], msg=result.get("error"))
		return result["data"]

	def test_express_admin_reads_dashboard_without_admin_scope(self):
		"""The whole point of the split: the tab opens, the data scope does not widen.

		An Express Admin holds no standard role and cannot be given one, so before the
		split this user was locked out of the dashboard entirely.
		"""
		data = self._payload("All", "Express Admin")
		self.assertTrue(data["can_view_sales_dashboard"])
		self.assertFalse(data["is_admin_user"])

	def test_sales_manager_gets_both(self):
		data = self._payload("All", "Sales Manager")
		self.assertTrue(data["can_view_sales_dashboard"])
		self.assertTrue(data["is_admin_user"])

	def test_system_manager_gets_both(self):
		data = self._payload("All", "System Manager")
		self.assertTrue(data["can_view_sales_dashboard"])
		self.assertTrue(data["is_admin_user"])

	def test_plain_cashier_gets_neither(self):
		data = self._payload("All", "Sales User")
		self.assertFalse(data["can_view_sales_dashboard"])
		self.assertFalse(data["is_admin_user"])

	def test_other_express_roles_do_not_read_the_dashboard(self):
		"""Only Express Admin was granted this. Stocks/Purchase/Sales stay out."""
		for role in ("Express Sales", "Express Stocks", "Express Purchase"):
			with self.subTest(role=role):
				data = self._payload("All", role)
				self.assertFalse(data["can_view_sales_dashboard"])
				self.assertFalse(data["is_admin_user"])

	def test_role_name_is_never_looked_up(self):
		"""klik_pos installs without erpnext_express, where no Express role exists.

		The check is a string comparison against the caller's roles - never a Role
		lookup - so a site missing the role simply never matches. Nothing to import,
		nothing to fail.
		"""
		self.assertNotIn("Express Admin", frappe.get_all("Role", pluck="name", filters={"name": "ZZ absent"}))
		data = self._payload("All")
		self.assertFalse(data["can_view_sales_dashboard"])
		self.assertFalse(data["is_admin_user"])

	def test_admin_roles_membership_is_unchanged(self):
		"""Regression pin: the split must not widen who counts as an admin.

		`is_admin_user` still governs the cashier filter (InvoiceHistory.tsx:236) and the
		POS-profile filter (DashboardPage.tsx:124). Adding a role here grants cross-till
		visibility, which is not what dashboard access is for.
		"""
		self.assertEqual(ADMIN_ROLES, ["Administrator", "Sales Manager", "System Manager"])

	def test_dashboard_roles_is_admin_roles_plus_express_admin(self):
		self.assertEqual(DASHBOARD_ROLES, [*ADMIN_ROLES, "Express Admin"])

	def test_both_endpoints_expose_the_flag(self):
		"""The nav components read get_current_user_info; DashboardPage's backstop too."""
		data = self._payload("All", "Express Admin")
		self.assertIn("can_view_sales_dashboard", data)

		with _with_roles("All", "Express Admin"):
			result = get_current_user_info()
		self.assertTrue(result["success"], msg=result.get("error"))
		self.assertTrue(result["data"]["can_view_sales_dashboard"])
		self.assertFalse(result["data"]["is_admin_user"])

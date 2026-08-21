"""Who sees other people's tills, and who only sees their own.

klik_pos branched on roles in six places and had drifted into five different spellings
of one idea. The visible symptom: the frontend enabled the cashier dropdown for a Sales
Manager (`api/user.py`) while the invoice query refused to fill it, because
`get_sales_invoices` had its own list that omitted Sales Manager. An enabled control
over data that was never returned.

`klik_pos.roles` is now the single source. ADMIN_ROLES means "sees every till" and is
used by every call site, so the control and the data can no longer disagree.

Express Admin is in it: on an ERPNext Express deployment it is the back-office operator,
and Express's role hygiene hook (erpnext_express/user_hooks.py) forbids giving an
Express user a standard role alongside, so naming the role is the only way such a site
can have a manager at all. Express Sales/Stocks/Purchase are deliberately absent - a
till user sees their own documents only.

None of this costs anything on a plain klik_pos install. Every check is a string
comparison against the caller's roles, never a Role lookup, so an absent name simply
never matches. test_role_name_is_never_looked_up pins that.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.payment import _check_admin_privileges
from klik_pos.api.sales_invoice import _is_admin_user as invoice_scope_is_admin
from klik_pos.api.user import get_current_user_info, get_user_roles
from klik_pos.roles import (
	ADMIN_ROLES,
	ALERT_ROLES,
	DASHBOARD_ROLES,
	can_view_sales_dashboard,
	is_admin_user,
)

TILL_ROLES = ("Express Sales", "Express Stocks", "Express Purchase")


def _with_roles(*roles):
	"""Run as a user holding exactly these roles."""
	return patch("frappe.get_roles", return_value=list(roles))


class TestManagerRoleMembership(FrappeTestCase):
	def test_express_admin_is_a_manager(self):
		"""The correction: Express Admin sees everyone's documents, not just its own."""
		with _with_roles("All", "Express Admin"):
			self.assertTrue(is_admin_user())
			self.assertTrue(can_view_sales_dashboard())

	def test_express_till_roles_see_only_their_own(self):
		for role in TILL_ROLES:
			with self.subTest(role=role), _with_roles("All", role):
				self.assertFalse(is_admin_user())
				self.assertFalse(can_view_sales_dashboard())

	def test_standard_manager_roles_still_qualify(self):
		for role in ("Sales Manager", "System Manager"):
			with self.subTest(role=role), _with_roles("All", role):
				self.assertTrue(is_admin_user())
				self.assertTrue(can_view_sales_dashboard())

	def test_plain_cashier_qualifies_for_neither(self):
		with _with_roles("All", "Sales User"):
			self.assertFalse(is_admin_user())
			self.assertFalse(can_view_sales_dashboard())

	def test_admin_roles_membership_is_pinned(self):
		"""Adding a name here grants visibility over other people's takings."""
		self.assertEqual(
			ADMIN_ROLES, ["Administrator", "Sales Manager", "System Manager", "Express Admin"]
		)

	def test_dashboard_roles_currently_match_admin_roles(self):
		self.assertEqual(DASHBOARD_ROLES, ADMIN_ROLES)

	def test_alert_roles_exclude_administrator(self):
		"""ALERT_ROLES is resolved through a `Has Role` query, where "Administrator"
		means the literal system account rather than "anyone with full rights".
		Including it would mail a login that reads no POS alerts."""
		self.assertNotIn("Administrator", ALERT_ROLES)
		self.assertIn("Express Admin", ALERT_ROLES)


class TestNoExpressInstall(FrappeTestCase):
	"""klik_pos must be unchanged on a site without erpnext_express."""

	def test_role_name_is_never_looked_up(self):
		"""No Role lookup, no import of erpnext_express - just a string comparison.

		Patching get_roles to a plain cashier's set is exactly the situation on a
		non-Express site: the Express name is present in ADMIN_ROLES and matches nobody.
		"""
		with _with_roles("All", "Sales User"):
			self.assertFalse(is_admin_user())
			self.assertFalse(can_view_sales_dashboard())
			self.assertFalse(_check_admin_privileges())

	def test_standard_roles_are_unaffected_by_the_express_entry(self):
		"""Membership for the three standard names is what it was before Express existed."""
		for role in ("Administrator", "Sales Manager", "System Manager"):
			with self.subTest(role=role), _with_roles("All", role):
				self.assertTrue(is_admin_user())


class TestEveryCallSiteAgrees(FrappeTestCase):
	"""The bug this refactor closes: a control enabled over data never returned."""

	def _sites(self):
		"""Every manager-tier gate, keyed by what it governs."""
		return {
			"frontend flag (cashier dropdown, POS-profile filter)": lambda: get_user_roles()[
				"data"
			]["is_admin_user"],
			"invoice list scope (get_sales_invoices)": invoice_scope_is_admin,
			"payment totals (whole day vs own till)": _check_admin_privileges,
		}

	def test_a_manager_gets_the_same_answer_everywhere(self):
		for role in ("Sales Manager", "System Manager", "Express Admin"):
			for label, gate in self._sites().items():
				with self.subTest(role=role, gate=label), _with_roles("All", role):
					self.assertTrue(gate(), msg=f"{role} denied by {label}")

	def test_a_till_user_is_narrowed_everywhere(self):
		for role in ("Sales User", *TILL_ROLES):
			for label, gate in self._sites().items():
				with self.subTest(role=role, gate=label), _with_roles("All", role):
					self.assertFalse(gate(), msg=f"{role} allowed by {label}")

	def test_sales_manager_no_longer_disagrees_with_itself(self):
		"""Regression for the original defect: the dropdown was enabled and the query
		was not. Both sides now read ADMIN_ROLES."""
		with _with_roles("All", "Sales Manager"):
			frontend = get_user_roles()["data"]["is_admin_user"]
			backend = invoice_scope_is_admin()
		self.assertTrue(frontend)
		self.assertEqual(frontend, backend)


class TestUserEndpointPayload(FrappeTestCase):
	def test_both_endpoints_expose_both_flags(self):
		with _with_roles("All", "Express Admin"):
			roles_payload = get_user_roles()
			info_payload = get_current_user_info()

		for result in (roles_payload, info_payload):
			self.assertTrue(result["success"], msg=result.get("error"))
			data = result["data"]
			self.assertTrue(data["can_view_sales_dashboard"])
			self.assertTrue(data["is_admin_user"])

	def test_admin_roles_is_still_reported_to_the_spa(self):
		"""`admin_roles` is part of the response shape the SPA types expect."""
		with _with_roles("All", "Sales User"):
			data = get_user_roles()["data"]
		self.assertEqual(data["admin_roles"], ADMIN_ROLES)

	def test_constant_holds_the_name_whether_or_not_the_role_exists(self):
		"""ADMIN_ROLES is a constant; the Role existing is a per-site fact. klik_pos
		reads the first and never queries the second."""
		self.assertIn("Express Admin", ADMIN_ROLES)
		with patch("frappe.db.exists", side_effect=AssertionError("no Role lookup allowed")):
			with _with_roles("All", "Express Admin"):
				self.assertTrue(is_admin_user())

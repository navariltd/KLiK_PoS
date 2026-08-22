"""Four screens share get_sales_invoices and they do not want the same scoping.

Invoice History, the Sales Dashboard, Closing Shift and the customer invoice list all
call one endpoint. Before `surface`, it guessed from the other arguments and got two
things wrong:

  - The Dashboard was gated in the SPA only. `is_admin_user` there came from a different
    role list than the one the query used, so the tab opened onto a query that scoped to
    the viewer's own opening entry - a blank dashboard for a Sales Manager or an Express
    Admin, and no server-side gate at all for anyone calling the endpoint directly.

  - Invoice History's "own invoices only" restriction was enforced by the page sending
    its own name as `cashier_name`. Omit that parameter and the restriction vanished; a
    probe as a real Express Admin returned 80 invoices belonging to another user.

Each screen now says which it is. Closing Shift and the customer list say nothing and
keep their old behaviour deliberately: a shift legitimately spans cashiers on a shared
till, and a customer's invoices were rung by whoever served them.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_invoice import (
	_can_view_sales_dashboard,
	_profile_allows_other_cashiers,
	get_sales_invoices,
)


def _with_roles(*roles):
	return patch("frappe.get_roles", return_value=list(roles))


def _profile(allow):
	"""Stand in for the current POS Profile with the flag on or off."""
	return patch(
		"klik_pos.api.sales_invoice.get_current_pos_profile",
		return_value=frappe._dict(
			{"name": "Test Profile", "custom_allow_viewing_other_cashiers": allow}
		),
	)


class TestProfileFlag(FrappeTestCase):
	def test_flag_on_allows_other_cashiers(self):
		self.assertTrue(
			_profile_allows_other_cashiers(frappe._dict({"custom_allow_viewing_other_cashiers": 1}))
		)

	def test_flag_off_restricts(self):
		self.assertFalse(
			_profile_allows_other_cashiers(frappe._dict({"custom_allow_viewing_other_cashiers": 0}))
		)

	def test_missing_field_reads_as_off(self):
		"""A site that has not migrated yet must keep today's behaviour, not open up."""
		self.assertFalse(_profile_allows_other_cashiers(frappe._dict({"name": "P"})))

	def test_missing_profile_reads_as_off(self):
		self.assertFalse(_profile_allows_other_cashiers(None))

	def test_it_is_a_till_property_not_a_role_one(self):
		"""Even a System Manager is held to their own invoices on a restricted till."""
		doc = frappe._dict({"custom_allow_viewing_other_cashiers": 0})
		with _with_roles("All", "System Manager"):
			self.assertFalse(_profile_allows_other_cashiers(doc))


class TestDashboardIsServerSideGated(FrappeTestCase):
	"""The SPA gate is convenience; this is the guard."""

	def test_a_till_user_calling_the_dashboard_surface_is_refused(self):
		with _with_roles("All", "Sales User"):
			result = get_sales_invoices(surface="dashboard")
		self.assertFalse(result["success"])
		self.assertIn("Sales Dashboard", result["error"])

	def test_express_till_roles_are_refused(self):
		for role in ("Express Sales", "Express Stocks", "Express Purchase"):
			with self.subTest(role=role), _with_roles("All", role):
				self.assertFalse(get_sales_invoices(surface="dashboard")["success"])

	def test_managers_are_admitted(self):
		for role in ("Sales Manager", "System Manager", "Express Admin"):
			with self.subTest(role=role), _with_roles("All", role):
				self.assertTrue(_can_view_sales_dashboard(["All", role]))
				self.assertTrue(get_sales_invoices(surface="dashboard", limit=1)["success"])

	def test_the_gate_matches_the_list_the_spa_is_served(self):
		"""Drift between these two is what produced the blank dashboard."""
		from klik_pos.api.user import DASHBOARD_ROLES

		for role in DASHBOARD_ROLES:
			with self.subTest(role=role):
				self.assertTrue(_can_view_sales_dashboard([role]))


class TestSurfaceShapesTheQuery(FrappeTestCase):
	"""Assert on the SQL actually built, so a scoping change cannot pass unnoticed."""

	def _sql_for(self, **kwargs):
		captured = []
		real = frappe.db.sql

		def spy(query, values=None, *a, **kw):
			captured.append(str(query))
			return real(query, values, *a, **kw)

		with patch("frappe.db.sql", side_effect=spy):
			get_sales_invoices(limit=1, **kwargs)
		return " ".join(captured)

	def test_dashboard_restricts_nothing(self):
		"""Assert on the conditions, which carry the `si.` prefix - the bare column name
		also appears in the SELECT list, where it means nothing."""
		with _with_roles("All", "Express Admin"), _profile(0):
			sql = self._sql_for(surface="dashboard")
		self.assertNotIn("si.owner = ", sql)
		self.assertNotIn("si.pos_profile = ", sql)
		self.assertNotIn("si.custom_pos_opening_entry", sql)
		self.assertNotIn("WHERE si.", sql)

	def test_history_applies_the_owner_filter_when_the_till_says_so(self):
		with _with_roles("All", "Express Admin"), _profile(0):
			sql = self._sql_for(surface="history", skip_opening_entry_filter=True)
		self.assertIn("si.owner = ", sql)

	def test_history_drops_the_owner_filter_when_the_till_allows(self):
		with _with_roles("All", "Sales User"), _profile(1):
			sql = self._sql_for(surface="history", skip_opening_entry_filter=True)
		self.assertNotIn("si.owner = ", sql)

	def test_unspecified_surface_is_untouched(self):
		"""Closing Shift and the customer invoice list must not gain an owner filter:
		a shift spans cashiers on a shared till, and a customer was served by whoever
		served them."""
		with _with_roles("All", "Sales User"), _profile(0):
			sql = self._sql_for()
		self.assertNotIn("si.owner = ", sql)


class TestNoExpressInstall(FrappeTestCase):
	def test_nothing_here_looks_a_role_up(self):
		"""On a klik_pos install without erpnext_express the name matches nobody and
		every other role behaves exactly as before."""
		with patch("frappe.db.exists", side_effect=AssertionError("no Role lookup allowed")):
			self.assertTrue(_can_view_sales_dashboard(["Sales Manager"]))
			self.assertFalse(_can_view_sales_dashboard(["Sales User"]))

	def test_a_plain_cashier_is_unaffected_by_the_express_entry(self):
		with _with_roles("All", "Sales User"):
			self.assertFalse(_can_view_sales_dashboard(frappe.get_roles()))

"""The POS should say what a role cannot reach, before a wrong number costs a sale.

Companion to test_permission_degradation: that pins what get_items reports once a query has
already run; this pins the preflight that covers every other module at once.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api import permission_health
from klik_pos.api.permission_health import (
	CRITICAL,
	DEGRADED,
	get_permission_health,
)


class TestPermissionHealth(FrappeTestCase):
	def _health_with(self, denied):
		"""Run the check with `denied` = {(doctype, ptype), ...} refused."""

		def fake_has_permission(doctype, ptype="read", *args, **kwargs):
			return (doctype, ptype) not in denied

		with patch.object(permission_health.frappe, "has_permission", side_effect=fake_has_permission):
			return get_permission_health()

	def test_a_fully_permitted_user_is_healthy(self):
		result = self._health_with(denied=set())

		self.assertTrue(result["healthy"])
		self.assertEqual(result["missing"], [])
		self.assertFalse(result["has_critical"])

	def test_a_missing_stock_permission_is_reported_as_degraded(self):
		result = self._health_with(denied={("Bin", "read")})

		self.assertFalse(result["healthy"])
		self.assertFalse(result["has_critical"], "missing stock degrades the POS, it does not kill it")

		entries = [e for e in result["missing"] if e["doctype"] == "Bin"]
		self.assertEqual(len(entries), 1)
		self.assertEqual(entries[0]["severity"], DEGRADED)
		self.assertIn("Stock levels are unknown", entries[0]["consequence"])

	def test_a_missing_catalogue_permission_is_critical(self):
		result = self._health_with(denied={("Item", "read")})

		self.assertTrue(result["has_critical"])
		entries = [e for e in result["missing"] if e["doctype"] == "Item"]
		self.assertEqual(entries[0]["severity"], CRITICAL)

	def test_it_names_roles_that_actually_grant_the_permission(self):
		"""A guessed role name is worse than none - it sends the admin to the wrong switch."""
		result = self._health_with(denied={("Bin", "read")})
		entry = next(e for e in result["missing"] if e["doctype"] == "Bin")

		self.assertTrue(entry["granting_roles"], "no role offered as a remedy")
		self.assertNotIn("Administrator", entry["granting_roles"], "holds everything; not a remedy")

		# Every named role must really carry read on Bin, via whichever table is in force.
		source = "Custom DocPerm" if frappe.db.exists("Custom DocPerm", {"parent": "Bin"}) else "DocPerm"
		actual = set(
			frappe.get_all(source, filters={"parent": "Bin", "permlevel": 0, "read": 1}, pluck="role")
		)
		self.assertTrue(set(entry["granting_roles"]).issubset(actual))

	def test_several_gaps_are_all_reported(self):
		result = self._health_with(denied={("Bin", "read"), ("Item Price", "read")})

		reported = {e["doctype"] for e in result["missing"]}
		self.assertEqual(reported, {"Bin", "Item Price"})

	def test_a_doctype_absent_from_the_site_is_not_a_permission_problem(self):
		def fake_exists(doctype, name=None, *args, **kwargs):
			if doctype == "DocType" and name == "Bin":
				return None
			return frappe.db.exists(doctype, name) if name is not None else frappe.db.exists(doctype)

		with (
			patch.object(
				permission_health.frappe,
				"has_permission",
				side_effect=lambda dt, pt="read", *a, **k: dt != "Bin",
			),
			patch.object(permission_health.frappe.db, "exists", side_effect=fake_exists),
		):
			result = get_permission_health()

		self.assertNotIn("Bin", {e["doctype"] for e in result["missing"]})

	def test_the_check_never_raises(self):
		"""A health check that can break the POS is worse than no health check."""
		with patch.object(permission_health.frappe, "has_permission", side_effect=Exception("boom")):
			result = get_permission_health()

		self.assertTrue(result["healthy"], "an unusable check must not invent failures")

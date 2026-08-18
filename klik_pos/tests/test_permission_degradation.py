"""A permission gap must announce itself, not quietly serve wrong numbers.

apply_sql_permissions degrades a denied doctype to `0=1` so one missing perm on a peripheral
doctype cannot take the whole POS down. That is right; doing it silently was not. Without a
`Bin` read every stock balance came back as zero, and with hide_unavailable_items on the
Python filter then dropped every single item - an empty shop, no error, no explanation. That
is what stranded a production till.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.item import item_listing
from klik_pos.api.sql_builder import (
	apply_sql_permissions,
	describe_denied_doctypes,
	get_denied_doctypes,
	reset_denied_doctypes,
)

ITEM_GROUP = "TEST-DEGRADE-GROUP"
ITEM_CODES = [f"TEST-DEGRADE-ITEM-{i:03d}" for i in range(1, 4)]
NO_STOCK_ROLE = "ZZ Test No Bin Access"
NO_STOCK_USER = "degrade_no_bin@example.com"


class TestDenialRecording(FrappeTestCase):
	def setUp(self):
		reset_denied_doctypes()

	def test_a_denied_doctype_is_recorded_and_still_degrades(self):
		"""Observability must not change the degradation itself."""
		with patch(
			"klik_pos.api.sql_builder.build_match_conditions",
			side_effect=frappe.PermissionError("nope"),
		):
			rewritten = apply_sql_permissions("SELECT name FROM `tabBin` WHERE warehouse = %s")

		self.assertIn("0=1", rewritten, "the query must still be neutered, not left open")
		self.assertIn("Bin", get_denied_doctypes())

	def test_nothing_is_recorded_when_permissions_are_fine(self):
		apply_sql_permissions("SELECT name FROM `tabBin` WHERE warehouse = %s")
		self.assertEqual(get_denied_doctypes(), frozenset())

	def test_reset_clears_the_record(self):
		with patch(
			"klik_pos.api.sql_builder.build_match_conditions",
			side_effect=frappe.PermissionError("nope"),
		):
			apply_sql_permissions("SELECT name FROM `tabBin`")
		self.assertTrue(get_denied_doctypes())

		reset_denied_doctypes()
		self.assertEqual(get_denied_doctypes(), frozenset())

	def test_denied_doctypes_are_described_readably(self):
		self.assertEqual(describe_denied_doctypes([]), "")
		self.assertEqual(describe_denied_doctypes(["Bin"]), "Bin")
		self.assertEqual(describe_denied_doctypes(["Warehouse", "Bin"]), "Bin and Warehouse")
		self.assertEqual(
			describe_denied_doctypes(["Warehouse", "Bin", "Item Price"]),
			"Bin, Item Price and Warehouse",
		)


class TestGetItemsDegradesVisibly(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse, cls.company = frappe.db.get_value(
			"Warehouse", {"is_group": 0, "company": ["is", "set"]}, ["name", "company"]
		)

		if not frappe.db.exists("Item Group", ITEM_GROUP):
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": ITEM_GROUP,
					"parent_item_group": "All Item Groups",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)

		for code in ITEM_CODES:
			if frappe.db.exists("Item", code):
				continue
			item = frappe.new_doc("Item")
			item.item_code = code
			item.item_name = code
			item.item_group = ITEM_GROUP
			item.stock_uom = "Nos"
			item.is_stock_item = 1
			item.is_sales_item = 1
			item.insert(ignore_permissions=True)

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for code in ITEM_CODES:
			if frappe.db.exists("Item", code):
				frappe.delete_doc("Item", code, force=True, ignore_permissions=True)
		if frappe.db.exists("Item Group", ITEM_GROUP):
			frappe.delete_doc("Item Group", ITEM_GROUP, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def _pos_context(self, hide_unavailable):
		pos_doc = frappe._dict(
			name="TEST-DEGRADE-PROFILE",
			company=self.company,
			warehouse=self.warehouse,
			selling_price_list=frappe.db.get_value("Price List", {"selling": 1}, "name"),
			item_groups=[],
			custom_enable_service_items=0,
			custom_enhanced_search=0,
			is_tax_included_in_basic_rate=0,
			taxes_and_charges=None,
		)
		return pos_doc, self.warehouse, pos_doc.selling_price_list, hide_unavailable

	def _call(self, hide_unavailable=True, bin_readable=False, **kwargs):
		kwargs.setdefault("category", ITEM_GROUP)

		def fake_has_permission(doctype, *args, **inner):
			if doctype == "Bin":
				return bin_readable
			return True

		with (
			patch.object(item_listing, "_get_pos_context", return_value=self._pos_context(hide_unavailable)),
			patch.object(item_listing.frappe, "has_permission", side_effect=fake_has_permission),
			patch.object(
				item_listing,
				"_fetch_batch_stock",
				side_effect=lambda codes, warehouse: {code: 0 for code in codes},
			),
		):
			return item_listing.get_items(**kwargs)

	def test_the_catalogue_is_not_hidden_when_stock_cannot_be_read(self):
		"""The production failure: every balance reads zero, so the filter emptied the shop."""
		result = self._call(hide_unavailable=True, bin_readable=False)

		self.assertEqual(
			len(result["items"]),
			len(ITEM_CODES),
			"hide_unavailable_items must stand down when stock is unknown",
		)

	def test_the_response_says_what_went_wrong(self):
		result = self._call(hide_unavailable=True, bin_readable=False)

		self.assertTrue(result["degraded"])
		self.assertTrue(result["stock_unavailable"])
		self.assertIn("Bin", result["degraded_reason"])

	def test_the_filter_still_applies_when_stock_is_readable(self):
		"""Standing the filter down must be conditional, not a way of disabling it."""
		result = self._call(hide_unavailable=True, bin_readable=True)

		self.assertEqual(result["items"], [], "zero-stock items should still be filtered out")
		self.assertFalse(result["degraded"])
		self.assertFalse(result["stock_unavailable"])

	def test_a_healthy_response_is_not_flagged(self):
		result = self._call(hide_unavailable=False, bin_readable=True)

		self.assertTrue(result["items"])
		self.assertFalse(result["degraded"])
		self.assertIsNone(result["degraded_reason"])


class TestWarehouseDenialDoesNotKillTheEndpoint(FrappeTestCase):
	def test_pos_context_degrades_instead_of_raising(self):
		"""An unreadable Warehouse used to escape get_items' try block as a bare 403."""
		reset_denied_doctypes()

		with (
			patch.object(item_listing, "get_current_pos_profile", return_value=frappe._dict({})),
			patch.object(item_listing.frappe.db, "get_single_value", return_value=None),
			patch.object(item_listing.frappe, "get_list", side_effect=frappe.PermissionError("no warehouse")),
		):
			_pos_doc, warehouse, _price_list, _hide_unavailable = item_listing._get_pos_context()

		self.assertIsNone(warehouse)
		self.assertIn("Warehouse", get_denied_doctypes())

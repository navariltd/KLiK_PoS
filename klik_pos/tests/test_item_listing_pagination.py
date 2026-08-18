"""get_items must never hand the client a cursor that cannot advance.

The product grid pages with an IntersectionObserver sentinel: while `has_more` is true and
the sentinel is on screen it keeps calling load-more. So a response that reports "more
available" without moving the cursor forward is not a cosmetic bug, it is an unbounded
request loop against production.

That is what `hide_unavailable_items` used to trigger. `has_more` was derived from the
pre-filter SQL count while the client advanced its offset by the number of items that
survived the post-query Python filter, so a page whose items were all out of stock returned
zero items, `has_more: true`, and no progress - forever, at offset 0. The trigger in the
field was a user with no `Bin` read permission (erpnext_express had deleted the standard
role's grant), which made every balance read as zero and filtered out every single item.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.item import item_listing

ITEM_GROUP = "TEST-PAGINATION-GROUP"
ITEM_CODES = [f"TEST-PAGINATION-ITEM-{i:03d}" for i in range(1, 6)]


def _pos_context(hide_unavailable):
	company = frappe.db.get_value("Company", {}, "name")
	warehouse = frappe.db.get_value("Warehouse", {"is_group": 0, "company": company}, "name")
	pos_doc = frappe._dict(
		name="TEST-PAGINATION-PROFILE",
		company=company,
		warehouse=warehouse,
		selling_price_list=frappe.db.get_value("Price List", {"selling": 1}, "name"),
		item_groups=[],
		custom_enable_service_items=0,
		custom_enhanced_search=0,
		is_tax_included_in_basic_rate=0,
		taxes_and_charges=None,
	)
	return pos_doc, warehouse, pos_doc.selling_price_list, hide_unavailable


class ItemListingPaginationTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")
		cls.warehouse = frappe.db.get_value("Warehouse", {"is_group": 0, "company": cls.company}, "name")

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
			if not frappe.db.exists("Item", code):
				item = frappe.new_doc("Item")
				item.item_code = code
				item.item_name = code
				item.item_group = ITEM_GROUP
				item.stock_uom = "Nos"
				item.is_stock_item = 1
				item.is_sales_item = 1
				item.insert(ignore_permissions=True)

			# Real stock in tabBin, so the item passes the hide_unavailable SQL window and
			# can only be dropped by the Python filter - which is precisely the production
			# scenario, where Bin holds stock but the caller's blinded read returns zero.
			bin_name = frappe.db.get_value("Bin", {"item_code": code, "warehouse": cls.warehouse}, "name")
			if not bin_name:
				bin_name = (
					frappe.get_doc({"doctype": "Bin", "item_code": code, "warehouse": cls.warehouse})
					.insert(ignore_permissions=True)
					.name
				)
			frappe.db.set_value("Bin", bin_name, "actual_qty", 10, update_modified=False)

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for code in ITEM_CODES:
			for bin_name in frappe.get_all("Bin", filters={"item_code": code}, pluck="name"):
				frappe.db.set_value("Bin", bin_name, "actual_qty", 0, update_modified=False)
				frappe.delete_doc("Bin", bin_name, force=True, ignore_permissions=True)
			if frappe.db.exists("Item", code):
				frappe.delete_doc("Item", code, force=True, ignore_permissions=True)
		if frappe.db.exists("Item Group", ITEM_GROUP):
			frappe.delete_doc("Item Group", ITEM_GROUP, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def _call(self, hide_unavailable, stock_value, **kwargs):
		"""get_items over the test item group only, with POS context and stock pinned.

		Scoped by category so the assertions describe this fixture rather than whatever
		else the site happens to hold.
		"""
		kwargs.setdefault("category", ITEM_GROUP)
		with (
			patch.object(item_listing, "_get_pos_context", return_value=_pos_context(hide_unavailable)),
			patch.object(
				item_listing,
				"_fetch_batch_stock",
				side_effect=lambda codes, warehouse: {code: stock_value for code in codes},
			),
		):
			return item_listing.get_items(**kwargs)


class TestPaginationCursorAlwaysAdvances(ItemListingPaginationTestCase):
	def test_a_fully_filtered_page_still_advances_the_cursor(self):
		"""The regression: zero items returned must not mean zero progress."""
		result = self._call(hide_unavailable=True, stock_value=0, limit=1, offset=0)

		self.assertEqual(result["items"], [], "no item has stock, so none should survive")
		self.assertGreater(
			result["next_offset"],
			0,
			"the SQL window consumed a row, so the cursor must move past it",
		)

	def test_paging_terminates_when_everything_is_filtered_out(self):
		"""Walk the cursor exactly as the SPA does and prove it reaches the end."""
		offset = 0
		seen_offsets = []

		for _ in range(50):
			result = self._call(hide_unavailable=True, stock_value=0, limit=1, offset=offset)
			seen_offsets.append(offset)

			if not result["has_more"]:
				break

			self.assertGreater(
				result["next_offset"],
				offset,
				f"cursor parked at {offset} while has_more stayed true - this is the loop",
			)
			offset = result["next_offset"]
		else:
			self.fail(f"pagination did not terminate; offsets walked: {seen_offsets}")

		self.assertEqual(len(seen_offsets), len(set(seen_offsets)), "an offset was requested twice")

	def test_cursor_advances_by_rows_consumed_not_items_returned(self):
		limit = 2
		result = self._call(hide_unavailable=True, stock_value=0, limit=limit, offset=0)

		self.assertEqual(result["page_count"], 0)
		self.assertEqual(
			result["next_offset"],
			limit,
			"next_offset must count the rows the query read, not the items it returned",
		)

	def test_unfiltered_page_advances_by_the_items_it_returned(self):
		limit = 2
		result = self._call(hide_unavailable=True, stock_value=5, limit=limit, offset=0)

		self.assertTrue(result["items"], "stocked items should survive the filter")
		self.assertEqual(result["next_offset"], len(result["items"]))
		self.assertEqual(result["page_count"], len(result["items"]))

	def test_exhausted_window_reports_no_more(self):
		result = self._call(hide_unavailable=True, stock_value=0, limit=1, offset=100000)

		self.assertEqual(result["items"], [])
		self.assertFalse(result["has_more"])
		self.assertEqual(result["next_offset"], 100000)


class TestPaginationCountsAreDistinct(ItemListingPaginationTestCase):
	def test_total_count_is_the_result_set_not_the_page(self):
		limit = 1
		result = self._call(hide_unavailable=False, stock_value=5, limit=limit, offset=0)

		self.assertEqual(len(result["items"]), limit)
		self.assertEqual(
			result["total_count"],
			len(ITEM_CODES),
			"total_count must describe the whole result set, not the current page",
		)

	def test_page_count_tracks_the_filtered_page(self):
		result = self._call(hide_unavailable=True, stock_value=0, limit=3, offset=0)

		self.assertEqual(result["page_count"], 0)
		self.assertEqual(result["total_count"], len(ITEM_CODES))

	def test_has_more_is_false_once_the_window_covers_everything(self):
		result = self._call(hide_unavailable=False, stock_value=5, limit=2000, offset=0)

		self.assertFalse(result["has_more"])

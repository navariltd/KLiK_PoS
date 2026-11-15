"""Smaller per-page costs, and two endpoints that should not answer the wrong caller.

- Stock and the cost column came from two reads of the same tabBin rows.
- The price list for a customer loaded the whole Customer twice and its group once.
- get_items and get_items_stock_batch answered guests (no data - permissions held - but a
  dozen queries per anonymous call).
- With hide-unavailable on, the stock batch left out items that sold out, so the till's
  30-second refresh never brought them to zero. It now reports zeros - for plain stock
  items only: a bundle's or variant template's availability is computed by the listing,
  and a Bin figure (0) would overwrite it, even on a till still running the old code.
"""

from unittest.mock import patch

import frappe

from klik_pos.api.item import item_listing, item_stock
from klik_pos.api.item.item_price import get_price_list_with_customer_priority
from klik_pos.testing import any_till_currency
from klik_pos.tests import listing_fixtures as fx
from klik_pos.tests.test_item_listing_query import ListingQueryCase


class TestSmallerPageCosts(ListingQueryCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		any_till_currency(cls)

	def test_one_bin_read_serves_stock_and_cost(self):
		stock = item_listing._fetch_batch_stock([fx.STOCKED], fx.WAREHOUSE)
		self.assertEqual(stock[fx.STOCKED], 5)
		self.assertEqual(stock.valuation_rates[fx.STOCKED], 12.5)

	def test_the_listing_takes_the_cost_from_that_read(self):
		with (
			patch.object(item_listing, "_get_pos_context", return_value=fx.pos_context(False)),
			patch.object(
				item_listing, "_fetch_batch_cost_price", side_effect=AssertionError("second Bin read")
			),
		):
			result = item_listing.get_items(category=fx.GROUP, item_codes=[fx.STOCKED], limit=5)
		self.assertEqual(result["items"][0]["cost_price"], 12.5)

	def test_the_price_list_is_read_without_loading_whole_documents(self):
		previous = frappe.db.get_value("Customer", "_Test Customer", "default_price_list")
		frappe.db.set_value("Customer", "_Test Customer", "default_price_list", "_Test Price List")
		# tearDownClass commits, so the shared test customer is put back explicitly.
		self.addCleanup(frappe.db.set_value, "Customer", "_Test Customer", "default_price_list", previous)
		with patch.object(item_listing.frappe, "get_doc", side_effect=AssertionError("get_doc")):
			chosen = get_price_list_with_customer_priority("_Test Customer")
		self.assertEqual(chosen, "_Test Price List")

	def test_neither_listing_endpoint_answers_guests(self):
		methods = (item_listing.get_items, item_stock.get_items_stock_batch)
		# A logged-in user still reaches both - this fails if the whitelist itself goes.
		for method in methods:
			frappe.is_whitelisted(method)
		frappe.set_user("Guest")
		try:
			for method in methods:
				with self.assertRaises(frappe.PermissionError):
					frappe.is_whitelisted(method)
		finally:
			frappe.set_user("Administrator")

	def test_the_stock_refresh_reports_sold_out_items_and_leaves_bundles_alone(self):
		profile = frappe._dict(warehouse=fx.WAREHOUSE, hide_unavailable_items=1)
		codes = [fx.EMPTY, fx.STOCKED, fx.BUNDLE, fx.TEMPLATE, fx.SERVICE]
		with patch.object(item_stock, "get_current_pos_profile", return_value=profile):
			stock = item_stock.get_items_stock_batch(",".join(codes))
		self.assertEqual(stock, {fx.EMPTY: 0, fx.STOCKED: 5})

	def test_the_stock_refresh_reads_the_warehouse_the_till_asks_for(self):
		# A till that may switch: another warehouse is checked (test_picked_warehouse).
		profile = frappe._dict(
			name="_Test Till", company=fx.COMPANY, warehouse=fx.WAREHOUSE,
			hide_unavailable_items=0, allow_warehouse_change=1,
		)
		with patch.object(item_stock, "get_current_pos_profile", return_value=profile):
			stock = item_stock.get_items_stock_batch(fx.ELSEWHERE, warehouse=fx.OTHER_WAREHOUSE)
			home = item_stock.get_items_stock_batch(fx.ELSEWHERE)
		self.assertEqual(stock, {fx.ELSEWHERE: 5})
		self.assertEqual(home, {fx.ELSEWHERE: 0})

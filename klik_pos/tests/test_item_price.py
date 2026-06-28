from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from klik_pos.api.item.item_price import get_item_prices_across_price_lists


class TestItemPricesAcrossPriceLists(FrappeTestCase):
    _PRICE_LISTS = {
        "price_lists": [
            {"name": "Standard", "currency": "USD"},
            {"name": "Retail", "currency": "USD"},
            {"name": "Wholesale", "currency": "USD"},
        ]
    }

    def _run(self, item_code="ITEM-1", uom=None, rates=None):
        rates = rates or {}

        def fake_sql(sql, params, as_dict=False):
            name = params[1]
            return rates.get(name, [])

        with patch("klik_pos.api.item.pricing.get_selling_price_lists", return_value=self._PRICE_LISTS), \
             patch("klik_pos.api.item.item_price.apply_sql_permissions", side_effect=lambda s: s), \
             patch("frappe.db.sql", side_effect=fake_sql):
            return get_item_prices_across_price_lists(item_code, uom=uom)

    def test_returns_one_entry_per_list_with_a_rate(self):
        out = self._run(rates={
            "Standard": [{"price_list_rate": 10.0, "currency": "USD"}],
            "Retail": [{"price_list_rate": 12.5, "currency": "USD"}],
        })
        self.assertEqual(out, [
            {"price_list": "Standard", "rate": 10.0, "currency": "USD"},
            {"price_list": "Retail", "rate": 12.5, "currency": "USD"},
        ])

    def test_omits_lists_without_a_rate(self):
        out = self._run(rates={"Retail": [{"price_list_rate": 12.5, "currency": "USD"}]})
        self.assertEqual([r["price_list"] for r in out], ["Retail"])

    def test_empty_when_no_prices_anywhere(self):
        self.assertEqual(self._run(rates={}), [])

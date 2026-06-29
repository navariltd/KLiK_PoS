import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.item.item_details import get_customer_sales_history


class TestCustomerSalesHistory(FrappeTestCase):
    def test_returns_empty_when_no_identifier(self):
        result = get_customer_sales_history()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["summary"]["order_count"], 0)
        self.assertEqual(result["summary"]["total_spent"], 0)
        self.assertIsNone(result["matched_by"])

    def test_blank_strings_are_treated_as_no_identifier(self):
        result = get_customer_sales_history(customer="", walkin_phone="")
        self.assertEqual(result["rows"], [])
        self.assertIsNone(result["matched_by"])

    def test_limit_is_clamped_to_max(self):
        # limit far above the cap must not raise and must be bounded
        result = get_customer_sales_history(customer="Nonexistent Customer XYZ", limit=9999)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["matched_by"], "customer")

    def test_matched_by_walkin_phone_when_only_phone_given(self):
        result = get_customer_sales_history(walkin_phone="+254700000000")
        self.assertEqual(result["matched_by"], "walkin_phone")
        self.assertIsInstance(result["rows"], list)

    def test_customer_takes_precedence_over_phone(self):
        result = get_customer_sales_history(customer="Nonexistent Customer XYZ", walkin_phone="+254700000000")
        self.assertEqual(result["matched_by"], "customer")

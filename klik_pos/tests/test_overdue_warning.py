import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.customer import get_customer_overdue_invoices


class TestGetCustomerOverdueInvoices(FrappeTestCase):
    def test_empty_customer_returns_no_overdue(self):
        result = get_customer_overdue_invoices(customer="")
        self.assertFalse(result["has_overdue"])
        self.assertEqual(result["invoices"], [])
        self.assertEqual(result["customer_name"], "")

    def test_nonexistent_customer_returns_empty(self):
        result = get_customer_overdue_invoices(customer="Nonexistent Customer XYZ 9999")
        self.assertFalse(result["has_overdue"])
        self.assertEqual(result["invoices"], [])

    def test_return_shape(self):
        result = get_customer_overdue_invoices(customer="Nonexistent Customer XYZ 9999")
        self.assertIn("has_overdue", result)
        self.assertIn("invoices", result)
        self.assertIn("customer_name", result)
        self.assertIsInstance(result["invoices"], list)

    def test_company_filter_accepted(self):
        # Should not raise even with a company that doesn't exist
        result = get_customer_overdue_invoices(
            customer="Nonexistent Customer XYZ 9999",
            company="Nonexistent Company"
        )
        self.assertFalse(result["has_overdue"])

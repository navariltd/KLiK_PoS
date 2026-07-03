from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.item.item_search import get_item_by_identifier


class TestGetItemByIdentifier(FrappeTestCase):
    def setUp(self):
        self.item_code = "TEST-BARCODE-ITEM-001"
        if frappe.db.exists("Item", self.item_code):
            frappe.delete_doc("Item", self.item_code, force=True, ignore_permissions=True)

        item = frappe.new_doc("Item")
        item.item_code = self.item_code
        item.item_name = self.item_code
        item.item_group = frappe.db.get_value("Item Group", {}, "name") or "All Item Groups"
        item.stock_uom = "Nos"
        item.append("barcodes", {"barcode": "TESTBARCODE123456"})
        item.insert(ignore_permissions=True)

    def test_matching_barcode_returns_item(self):
        result = get_item_by_identifier(code="TESTBARCODE123456")
        self.assertEqual(result["item_code"], self.item_code)
        self.assertEqual(result["matched_type"], "barcode")

    def test_unmatched_code_raises_does_not_exist(self):
        with self.assertRaises(frappe.DoesNotExistError):
            get_item_by_identifier(code="NO-SUCH-CODE-XYZ-9999")

    def test_unmatched_code_does_not_create_error_log(self):
        title = "Error fetching item by identifier: NO-SUCH-CODE-XYZ-9999"
        before = frappe.db.count("Error Log", {"method": title})

        with self.assertRaises(frappe.DoesNotExistError):
            get_item_by_identifier(code="NO-SUCH-CODE-XYZ-9999")

        after = frappe.db.count("Error Log", {"method": title})
        self.assertEqual(before, after, "A routine 'not found' lookup must not create an Error Log entry")


class TestGetItemByIdentifierPOSProfileErrors(FrappeTestCase):
    def test_pos_profile_resolution_failure_is_logged(self):
        title = "Error fetching item by identifier: ANY-CODE"
        before = frappe.db.count("Error Log", {"method": title})

        with patch(
            "klik_pos.api.item.item_search.get_current_pos_profile",
            side_effect=Exception("No POS Profile found for user test@example.com"),
        ):
            with self.assertRaises(frappe.ValidationError):
                get_item_by_identifier(code="ANY-CODE")

        after = frappe.db.count("Error Log", {"method": title})
        self.assertEqual(after, before + 1, "A genuine POS-profile resolution failure must still be logged")

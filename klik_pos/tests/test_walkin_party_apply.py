from types import SimpleNamespace
from unittest.mock import MagicMock

from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_invoice import _apply_walkin_party_fields


def _doc_with_fields(has):
    doc = SimpleNamespace()
    doc.meta = MagicMock()
    doc.meta.has_field.side_effect = lambda f: f in has
    return doc


class TestApplyWalkinFields(FrappeTestCase):
    def test_sets_fields_when_present(self):
        doc = _doc_with_fields({"custom_walkin_customer_name", "custom_walkin_phone"})
        _apply_walkin_party_fields(doc, walkin_name="Jane Doe", walkin_phone="0500000000")
        self.assertEqual(doc.custom_walkin_customer_name, "Jane Doe")
        self.assertEqual(doc.custom_walkin_phone, "0500000000")

    def test_skips_when_field_absent(self):
        doc = _doc_with_fields(set())
        _apply_walkin_party_fields(doc, walkin_name="Jane Doe", walkin_phone="0500000000")
        self.assertFalse(hasattr(doc, "custom_walkin_customer_name"))
        self.assertFalse(hasattr(doc, "custom_walkin_phone"))

    def test_skips_empty_values(self):
        doc = _doc_with_fields({"custom_walkin_customer_name", "custom_walkin_phone"})
        _apply_walkin_party_fields(doc, walkin_name="", walkin_phone=None)
        self.assertFalse(hasattr(doc, "custom_walkin_customer_name"))
        self.assertFalse(hasattr(doc, "custom_walkin_phone"))

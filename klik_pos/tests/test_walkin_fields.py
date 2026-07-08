from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from klik_pos.setup.walkin_fields import (
    WALKIN_DOCTYPES,
    WALKIN_FIELDS,
    install_walkin_fields,
    install_walkin_party_fields,
)


class TestWalkinFields(FrappeTestCase):
    def test_field_spec_is_two_data_fields_with_shared_names(self):
        names = [f["fieldname"] for f in WALKIN_FIELDS]
        self.assertEqual(names, ["custom_walkin_customer_name", "custom_walkin_phone"])
        for f in WALKIN_FIELDS:
            self.assertEqual(f["fieldtype"], "Data")
            self.assertEqual(f["no_copy"], 0)

    @patch("klik_pos.setup.walkin_fields.create_custom_fields")
    def test_install_builds_map_for_all_doctypes(self, mock_create):
        result = install_walkin_fields()
        self.assertEqual(result, WALKIN_DOCTYPES)
        sent_map = mock_create.call_args[0][0]
        self.assertEqual(set(sent_map.keys()), set(WALKIN_DOCTYPES))
        self.assertEqual(sent_map["Sales Invoice"], WALKIN_FIELDS)

    @patch("klik_pos.setup.walkin_fields.create_custom_fields")
    def test_install_respects_explicit_doctypes(self, mock_create):
        result = install_walkin_fields(["Sales Invoice"])
        self.assertEqual(result, ["Sales Invoice"])
        self.assertEqual(set(mock_create.call_args[0][0].keys()), {"Sales Invoice"})

    @patch("klik_pos.setup.walkin_fields.create_custom_fields")
    @patch("frappe.has_permission", return_value=True)
    def test_endpoint_installs_all_four(self, _perm, mock_create):
        result = install_walkin_party_fields()
        self.assertEqual(result["installed_on"], WALKIN_DOCTYPES)

    @patch("frappe.has_permission", return_value=False)
    def test_endpoint_denies_without_permission(self, _perm):
        with self.assertRaises(Exception):
            install_walkin_party_fields()

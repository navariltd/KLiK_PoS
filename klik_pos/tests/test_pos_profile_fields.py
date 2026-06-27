from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from klik_pos.setup.pos_profile_fields import (
    POS_PROFILE_FEATURE_FIELDS,
    install_pos_profile_feature_fields,
)


class TestPosProfileFeatureFields(FrappeTestCase):
    def test_spec_includes_both_toggle_fields(self):
        names = [f["fieldname"] for f in POS_PROFILE_FEATURE_FIELDS]
        self.assertIn("allow_price_list_switching", names)
        self.assertIn("allow_warehouse_change", names)
        for f in POS_PROFILE_FEATURE_FIELDS:
            self.assertEqual(f["fieldtype"], "Check")

    @patch("klik_pos.setup.pos_profile_fields.create_custom_fields")
    @patch("frappe.db.has_column", return_value=False)
    def test_creates_all_when_none_exist(self, _hc, mock_create):
        result = install_pos_profile_feature_fields()
        self.assertEqual(result, ["allow_price_list_switching", "allow_warehouse_change"])
        sent = mock_create.call_args[0][0]
        self.assertEqual(sent["POS Profile"], POS_PROFILE_FEATURE_FIELDS)
        self.assertTrue(mock_create.call_args.kwargs.get("update"))

    @patch("klik_pos.setup.pos_profile_fields.create_custom_fields")
    def test_skips_existing_standard_field(self, mock_create):
        # warehouse already exists (e.g. standard field), price-list missing
        with patch("frappe.db.has_column", side_effect=lambda dt, fn: fn == "allow_warehouse_change"):
            result = install_pos_profile_feature_fields()
        self.assertEqual(result, ["allow_price_list_switching"])
        sent = mock_create.call_args[0][0]
        self.assertEqual([f["fieldname"] for f in sent["POS Profile"]], ["allow_price_list_switching"])

    @patch("klik_pos.setup.pos_profile_fields.create_custom_fields")
    @patch("frappe.db.has_column", return_value=True)
    def test_noop_when_all_exist(self, _hc, mock_create):
        result = install_pos_profile_feature_fields()
        self.assertEqual(result, [])
        mock_create.assert_not_called()

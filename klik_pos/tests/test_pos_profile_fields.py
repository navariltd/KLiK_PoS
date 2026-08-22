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

    def test_spec_includes_sales_lens_toggle(self):
        names = [f["fieldname"] for f in POS_PROFILE_FEATURE_FIELDS]
        self.assertIn("custom_enable_sales_lens", names)
        lens = next(f for f in POS_PROFILE_FEATURE_FIELDS if f["fieldname"] == "custom_enable_sales_lens")
        self.assertEqual(lens["fieldtype"], "Check")
        self.assertEqual(lens["default"], "0")
        self.assertEqual(lens["module"], "KLiK PoS")

    def test_spec_includes_overdue_warning_toggle(self):
        names = [f["fieldname"] for f in POS_PROFILE_FEATURE_FIELDS]
        self.assertIn("custom_show_overdue_warning", names)
        f = next(x for x in POS_PROFILE_FEATURE_FIELDS if x["fieldname"] == "custom_show_overdue_warning")
        self.assertEqual(f["fieldtype"], "Check")
        self.assertEqual(f["default"], "0")
        self.assertEqual(f["module"], "KLiK PoS")

    def test_spec_includes_credit_sales_as_pos_toggle(self):
        names = [f["fieldname"] for f in POS_PROFILE_FEATURE_FIELDS]
        self.assertIn("custom_allow_credit_sales_as_pos", names)
        f = next(x for x in POS_PROFILE_FEATURE_FIELDS if x["fieldname"] == "custom_allow_credit_sales_as_pos")
        self.assertEqual(f["fieldtype"], "Check")
        self.assertEqual(f["default"], "0")
        self.assertEqual(f["module"], "KLiK PoS")

    def test_spec_includes_other_cashier_visibility_toggle(self):
        names = [f["fieldname"] for f in POS_PROFILE_FEATURE_FIELDS]
        self.assertIn("custom_allow_viewing_other_cashiers", names)
        f = next(x for x in POS_PROFILE_FEATURE_FIELDS if x["fieldname"] == "custom_allow_viewing_other_cashiers")
        self.assertEqual(f["fieldtype"], "Check")
        # Default off: an upgraded site must keep the visibility it has today.
        self.assertEqual(f["default"], "0")
        self.assertEqual(f["module"], "KLiK PoS")
        # insert_after must name a field created earlier in the same pass, or the
        # column lands in the wrong place on a fresh install.
        self.assertEqual(f["insert_after"], "custom_allow_credit_sales_as_pos")
        self.assertLess(
            names.index("custom_allow_credit_sales_as_pos"),
            names.index("custom_allow_viewing_other_cashiers"),
        )

    @patch("klik_pos.setup.pos_profile_fields.create_custom_fields")
    @patch("frappe.db.has_column", return_value=False)
    def test_creates_all_when_none_exist(self, _hc, mock_create):
        result = install_pos_profile_feature_fields()
        self.assertEqual(result, ["allow_price_list_switching", "allow_warehouse_change", "custom_enable_sales_lens", "custom_show_overdue_warning", "custom_allow_credit_sales_as_pos", "custom_allow_viewing_other_cashiers"])
        sent = mock_create.call_args[0][0]
        self.assertEqual(sent["POS Profile"], POS_PROFILE_FEATURE_FIELDS)
        self.assertTrue(mock_create.call_args.kwargs.get("update"))

    @patch("klik_pos.setup.pos_profile_fields.create_custom_fields")
    def test_skips_existing_standard_field(self, mock_create):
        # warehouse already exists (e.g. standard field), price-list and sales-lens missing
        with patch("frappe.db.has_column", side_effect=lambda dt, fn: fn == "allow_warehouse_change"):
            result = install_pos_profile_feature_fields()
        self.assertEqual(result, ["allow_price_list_switching", "custom_enable_sales_lens", "custom_show_overdue_warning", "custom_allow_credit_sales_as_pos", "custom_allow_viewing_other_cashiers"])
        sent = mock_create.call_args[0][0]
        self.assertEqual([f["fieldname"] for f in sent["POS Profile"]], ["allow_price_list_switching", "custom_enable_sales_lens", "custom_show_overdue_warning", "custom_allow_credit_sales_as_pos", "custom_allow_viewing_other_cashiers"])

    @patch("klik_pos.setup.pos_profile_fields.create_custom_fields")
    @patch("frappe.db.has_column", return_value=True)
    def test_noop_when_all_exist(self, _hc, mock_create):
        result = install_pos_profile_feature_fields()
        self.assertEqual(result, [])
        mock_create.assert_not_called()


class TestPosExtraFieldsChild(FrappeTestCase):
    def test_child_doctype_and_table_field_exist_after_install(self):
        from klik_pos.setup.pos_profile_fields import install_pos_extra_fields_child
        import frappe

        install_pos_extra_fields_child()
        self.assertTrue(frappe.db.exists("DocType", "POS Extra Field"))
        # Table fields don't create a physical column; verify the Custom Field record exists
        self.assertTrue(
            frappe.db.exists("Custom Field", {"dt": "POS Profile", "fieldname": "custom_pos_extra_fields"})
        )

    def test_install_is_idempotent(self):
        from klik_pos.setup.pos_profile_fields import install_pos_extra_fields_child
        # second call must not raise
        install_pos_extra_fields_child()
        install_pos_extra_fields_child()

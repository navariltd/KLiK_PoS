from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from klik_pos.api.pos_profile import _eligible_common_fields

WHITELIST = {"Select", "Link", "Data", "Small Text", "Int", "Float", "Check", "Date"}


def _f(fieldname, fieldtype, label=None, hidden=0, read_only=0, options=""):
    return SimpleNamespace(
        fieldname=fieldname, fieldtype=fieldtype, label=label or fieldname,
        hidden=hidden, read_only=read_only, options=options,
    )


class TestEligibleCommonFields(FrappeTestCase):
    def _meta(self, so_fields, si_fields):
        def fake_get_meta(dt):
            return SimpleNamespace(fields=so_fields if dt == "Sales Order" else si_fields)
        return fake_get_meta

    def test_returns_intersection_by_name_and_type(self):
        so = [_f("territory", "Link", options="Territory"), _f("only_so", "Data")]
        si = [_f("territory", "Link", options="Territory"), _f("only_si", "Data")]
        with patch("frappe.get_meta", side_effect=self._meta(so, si)):
            out = _eligible_common_fields()
        names = [f["fieldname"] for f in out]
        self.assertEqual(names, ["territory"])
        self.assertEqual(out[0]["fieldtype"], "Link")
        self.assertEqual(out[0]["options"], "Territory")

    def test_excludes_when_fieldtype_differs(self):
        so = [_f("x", "Link")]
        si = [_f("x", "Data")]
        with patch("frappe.get_meta", side_effect=self._meta(so, si)):
            out = _eligible_common_fields()
        self.assertEqual(out, [])

    def test_excludes_non_whitelisted_types(self):
        so = [_f("items", "Table"), _f("grand_total", "Currency")]
        si = [_f("items", "Table"), _f("grand_total", "Currency")]
        with patch("frappe.get_meta", side_effect=self._meta(so, si)):
            out = _eligible_common_fields()
        self.assertEqual(out, [])

    def test_excludes_hidden_readonly_and_system_fields(self):
        so = [_f("a", "Data", hidden=1), _f("b", "Data", read_only=1), _f("name", "Data")]
        si = [_f("a", "Data", hidden=1), _f("b", "Data", read_only=1), _f("name", "Data")]
        with patch("frappe.get_meta", side_effect=self._meta(so, si)):
            out = _eligible_common_fields()
        self.assertEqual(out, [])

    def test_keeps_po_no_as_data_field(self):
        so = [_f("po_no", "Data", label="Customer's Purchase Order")]
        si = [_f("po_no", "Data", label="Customer's Purchase Order")]
        with patch("frappe.get_meta", side_effect=self._meta(so, si)):
            out = _eligible_common_fields()
        self.assertEqual([f["fieldname"] for f in out], ["po_no"])

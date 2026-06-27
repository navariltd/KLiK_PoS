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

    def test_excludes_when_hidden_or_readonly_on_si_side(self):
        so = [_f("a", "Data"), _f("b", "Data")]
        si = [_f("a", "Data", hidden=1), _f("b", "Data", read_only=1)]
        with patch("frappe.get_meta", side_effect=self._meta(so, si)):
            out = _eligible_common_fields()
        self.assertEqual(out, [])

    def test_keeps_po_no_as_data_field(self):
        so = [_f("po_no", "Data", label="Customer's Purchase Order")]
        si = [_f("po_no", "Data", label="Customer's Purchase Order")]
        with patch("frappe.get_meta", side_effect=self._meta(so, si)):
            out = _eligible_common_fields()
        self.assertEqual([f["fieldname"] for f in out], ["po_no"])


from types import SimpleNamespace as NS

from klik_pos.api.sales_invoice import _apply_extra_fields, _parse_extra_fields


class _FakeDoc:
    def __init__(self, fields):
        self._fields = set(fields)
        self._set = {}
        self.meta = NS(has_field=lambda fn: fn in self._fields)

    def set(self, fieldname, value):
        self._set[fieldname] = value


class TestApplyExtraFields(FrappeTestCase):
    def test_sets_existing_nonempty_fields(self):
        doc = _FakeDoc({"territory", "po_no"})
        _apply_extra_fields(doc, {"territory": "Default", "po_no": "PO-1"})
        self.assertEqual(doc._set, {"territory": "Default", "po_no": "PO-1"})

    def test_skips_missing_field(self):
        doc = _FakeDoc({"territory"})
        _apply_extra_fields(doc, {"nope": "x"})
        self.assertEqual(doc._set, {})

    def test_skips_empty_values(self):
        doc = _FakeDoc({"territory", "po_no"})
        _apply_extra_fields(doc, {"territory": "", "po_no": None})
        self.assertEqual(doc._set, {})

    def test_tolerates_none(self):
        doc = _FakeDoc({"territory"})
        _apply_extra_fields(doc, None)
        self.assertEqual(doc._set, {})


class TestParseExtraFields(FrappeTestCase):
    def test_parses_dict(self):
        self.assertEqual(_parse_extra_fields({"extra_fields": {"a": "1"}}), {"a": "1"})

    def test_parses_json_string(self):
        self.assertEqual(_parse_extra_fields({"extra_fields": '{"a": "1"}'}), {"a": "1"})

    def test_missing_returns_empty(self):
        self.assertEqual(_parse_extra_fields({}), {})
        self.assertEqual(_parse_extra_fields("not-a-dict"), {})

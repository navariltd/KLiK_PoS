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
        _apply_extra_fields(doc, {"territory": "Default", "po_no": "PO-1"}, allowed={"territory", "po_no"})
        self.assertEqual(doc._set, {"territory": "Default", "po_no": "PO-1"})

    def test_skips_missing_field(self):
        doc = _FakeDoc({"territory"})
        _apply_extra_fields(doc, {"nope": "x"}, allowed={"nope"})
        self.assertEqual(doc._set, {})

    def test_skips_empty_values(self):
        doc = _FakeDoc({"territory", "po_no"})
        _apply_extra_fields(doc, {"territory": "", "po_no": None}, allowed={"territory", "po_no"})
        self.assertEqual(doc._set, {})

    def test_tolerates_none(self):
        doc = _FakeDoc({"territory"})
        _apply_extra_fields(doc, None, allowed={"territory"})
        self.assertEqual(doc._set, {})

    def test_skips_fields_not_in_allowed(self):
        doc = _FakeDoc({"territory", "additional_discount_percentage"})
        _apply_extra_fields(doc, {"territory": "X", "additional_discount_percentage": "90"}, allowed={"territory"})
        self.assertEqual(doc._set, {"territory": "X"})


class TestParseExtraFields(FrappeTestCase):
    def test_parses_dict(self):
        self.assertEqual(_parse_extra_fields({"extra_fields": {"a": "1"}}), {"a": "1"})

    def test_parses_json_string(self):
        self.assertEqual(_parse_extra_fields({"extra_fields": '{"a": "1"}'}), {"a": "1"})

    def test_missing_returns_empty(self):
        self.assertEqual(_parse_extra_fields({}), {})
        self.assertEqual(_parse_extra_fields("not-a-dict"), {})


import frappe

from klik_pos.api.pos_profile import validate_required_extra_fields


class TestRequiredExtraFields(FrappeTestCase):
    def _profile(self, rows):
        return NS(custom_pos_extra_fields=[NS(so_si_commonfield=f, reqd=r) for f, r in rows])

    def test_passes_when_required_present(self):
        prof = self._profile([("delivery_method", 1)])
        validate_required_extra_fields({"delivery_method": "Courier"}, pos_profile=prof)  # no raise

    def test_raises_when_required_missing(self):
        prof = self._profile([("delivery_method", 1)])
        with self.assertRaises(frappe.ValidationError):
            validate_required_extra_fields({}, pos_profile=prof)

    def test_ignores_optional_missing(self):
        prof = self._profile([("territory", 0)])
        validate_required_extra_fields({}, pos_profile=prof)  # no raise

    def test_configured_fieldnames_intersect_eligible(self):
        from unittest.mock import patch as _patch
        prof = NS(custom_pos_extra_fields=[NS(so_si_commonfield="territory"), NS(so_si_commonfield="evil_field")])
        with _patch("klik_pos.api.pos_profile._eligible_common_fields", return_value=[{"fieldname": "territory"}]):
            from klik_pos.api.pos_profile import get_configured_extra_fieldnames
            out = get_configured_extra_fieldnames(pos_profile=prof)
        self.assertEqual(out, {"territory"})


class TestSearchExtraFieldLink(FrappeTestCase):
    _LINK_CANDIDATES = [
        {"fieldname": "contact_person", "fieldtype": "Link", "options": "Contact", "label": "Contact Person"},
        {"fieldname": "territory", "fieldtype": "Link", "options": "Territory", "label": "Territory"},
        {"fieldname": "delivery_method", "fieldtype": "Select", "options": "A\nB", "label": "Delivery"},
    ]

    def test_rejects_non_eligible_doctype(self):
        from unittest.mock import patch as _patch
        from klik_pos.api.pos_profile import search_extra_field_link
        with _patch("klik_pos.api.pos_profile._eligible_common_fields", return_value=self._LINK_CANDIDATES):
            with self.assertRaises(frappe.ValidationError):
                search_extra_field_link("User", txt="x")  # User is not an eligible Link target

    def test_rejects_select_option_value_as_doctype(self):
        from unittest.mock import patch as _patch
        from klik_pos.api.pos_profile import search_extra_field_link
        with _patch("klik_pos.api.pos_profile._eligible_common_fields", return_value=self._LINK_CANDIDATES):
            with self.assertRaises(frappe.ValidationError):
                search_extra_field_link("A", txt="x")  # a Select option, not a Link target

    def test_accepts_eligible_link_target_and_maps_rows(self):
        from unittest.mock import patch as _patch
        from klik_pos.api.pos_profile import search_extra_field_link
        fake_meta = NS(title_field="")
        with _patch("klik_pos.api.pos_profile._eligible_common_fields", return_value=self._LINK_CANDIDATES), \
             _patch("frappe.get_meta", return_value=fake_meta), \
             _patch("frappe.get_list", return_value=[{"name": "CONT-001"}, {"name": "CONT-002"}]):
            out = search_extra_field_link("Contact", txt="co")
        self.assertEqual(out, [
            {"value": "CONT-001", "label": "CONT-001"},
            {"value": "CONT-002", "label": "CONT-002"},
        ])

# Configurable SO/SI Common Fields in POS Profile — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a POS Profile admin surface any field common to both Sales Order and Sales Invoice in the cashier-facing "Additional Info" dialog, write the chosen value through to the document, persist it across hold/resume, and optionally require it — retiring the bespoke `po_no` handling in favor of this generic mechanism. Also add F4 to open the dialog.

**Architecture:** A new child table on POS Profile (`custom_pos_extra_fields`) holds rows referencing SO∩SI common fieldnames. A whitelisted endpoint computes the eligible-field list (intersection by name+type, safe-fieldtype whitelist). The SPA renders one control per configured field in the Additional Info modal and carries values in a generic `extraFields` map through the existing cart_meta/payload plumbing. A guarded `_apply_extra_fields(doc, extra_fields)` helper sets them on both the Sales Order (hold) and Sales Invoice (checkout) documents. Required fields are enforced server-side.

**Tech Stack:** Frappe/ERPNext (Python), React + Zustand + TypeScript (Vite SPA), FrappeTestCase (backend tests), `npx tsc --noEmit` (frontend typecheck — no JS test runner in this app).

## Global Constraints

- Git commits: **no** `Co-Authored-By` / Claude attribution trailers (user global rule).
- Safe-fieldtype whitelist (verbatim): `Select, Link, Data, Small Text, Int, Float, Check, Date`.
- Field eligibility = present on **both** Sales Order and Sales Invoice, matched by **fieldname AND fieldtype**, excluding `hidden`, `read_only`, and standard system/amount fields.
- All POS Profile field/doctype installation must be **idempotent** and run through the existing `after_migrate` self-heal path (`klik_pos/setup/pos_profile_fields.py`).
- Guarded writes only: never set a field without `doc.meta.has_field(fieldname)`.
- Backend tests: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module <module>` (substitute the dev site). Frontend: `cd klik_spa && npx tsc --noEmit -p tsconfig.json`.
- Frontend dir: `/home/kushal/frappe-bench/apps/klik_pos/klik_spa`. Backend app dir: `/home/kushal/frappe-bench/apps/klik_pos/klik_pos`.

---

## File Structure

**Backend (`klik_pos/`):**
- `api/pos_profile.py` — add `get_pos_extra_field_candidates()` (eligibility endpoint) and `get_required_extra_fieldnames()` helper.
- `api/sales_invoice.py` — add `_apply_extra_fields()`, `_parse_extra_fields()`, `validate_required_extra_fields()`; thread `extra_fields`; remove bespoke `po_no`.
- `api/sales_order.py` — thread `extra_fields` into cart_meta + SO build/rebuild + resume payload; remove bespoke `po_no`.
- `setup/pos_profile_fields.py` — install child doctype `POS Extra Field` + child-table field `custom_pos_extra_fields`.
- `tests/test_pos_extra_fields.py` — new test module for endpoint + helpers.

**Frontend (`klik_spa/src/`):**
- `stores/cartStore.ts` — add `extraFields` state/setter; remove `poNo`.
- `components/order/WalkinInfoModal.tsx` — two-column layout, dynamic configured-field controls; remove PO input.
- `components/order/CustomerSearchSection.tsx` — wire `extraFields`, generalize chips, F4 listener; remove `poNo`.
- `components/dialog/PaymentDialog.tsx` — send `extra_fields`; remove `po_no`.
- `utils/heldOrderToCart.ts` — restore `extraFields`; remove `setPoNo`.
- `components/KeyboardShortcutsPanel.tsx` — add F4 entry.
- `hooks/useExtraFields.ts` (new) — resolve configured fields against candidate metadata.

---

## Task 1: Eligibility endpoint `get_pos_extra_field_candidates`

**Files:**
- Modify: `klik_pos/api/pos_profile.py`
- Test: `klik_pos/tests/test_pos_extra_fields.py` (create)

**Interfaces:**
- Produces: `get_pos_extra_field_candidates() -> list[dict]` where each dict is `{"fieldname": str, "label": str, "fieldtype": str, "options": str}`. Used by the SPA and the POS Profile client script.
- Produces: `_eligible_common_fields() -> list[dict]` (internal, same shape) — unit-tested directly.

- [ ] **Step 1: Write the failing test**

Create `klik_pos/tests/test_pos_extra_fields.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_extra_fields`
Expected: FAIL with `ImportError: cannot import name '_eligible_common_fields'`.

- [ ] **Step 3: Implement the endpoint + helper**

Add to `klik_pos/api/pos_profile.py` (top-level, after imports):

```python
EXTRA_FIELD_WHITELIST = {"Select", "Link", "Data", "Small Text", "Int", "Float", "Check", "Date"}

# Standard Frappe/transaction fields that pass the type whitelist but must never be
# offered for cashier entry.
EXTRA_FIELD_EXCLUDE = {
    "name", "owner", "docstatus", "idx", "naming_series", "amended_from",
    "title", "status", "company", "customer", "po_date",
}


def _eligible_common_fields():
    """SO∩SI fields eligible for POS Profile extra-field config.

    Intersect Sales Order and Sales Invoice fields by fieldname AND fieldtype,
    keep only safe whitelisted types, and drop hidden/read-only/system fields.
    """
    so = {f.fieldname: f for f in frappe.get_meta("Sales Order").fields}
    si = {f.fieldname: f for f in frappe.get_meta("Sales Invoice").fields}

    out = []
    for fieldname, sf in so.items():
        tf = si.get(fieldname)
        if tf is None or tf.fieldtype != sf.fieldtype:
            continue
        if sf.fieldtype not in EXTRA_FIELD_WHITELIST:
            continue
        if fieldname in EXTRA_FIELD_EXCLUDE:
            continue
        if getattr(sf, "hidden", 0) or getattr(sf, "read_only", 0):
            continue
        out.append({
            "fieldname": fieldname,
            "label": sf.label or fieldname,
            "fieldtype": sf.fieldtype,
            "options": sf.options or "",
        })
    out.sort(key=lambda f: f["label"].lower())
    return out


@frappe.whitelist()
def get_pos_extra_field_candidates():
    """Whitelisted: eligible SO∩SI common fields for the POS Profile picker + SPA."""
    return _eligible_common_fields()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_extra_fields`
Expected: PASS (5 tests). Note: `out.sort` does not affect the single-field assertions; `test_keeps_po_no` returns exactly `["po_no"]`.

- [ ] **Step 5: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_pos/api/pos_profile.py klik_pos/tests/test_pos_extra_fields.py
git commit -m "feat(pos): eligibility endpoint for SO/SI common extra fields"
```

---

## Task 2: Generic apply + parse helpers

**Files:**
- Modify: `klik_pos/api/sales_invoice.py` (near `_apply_walkin_party_fields`, ~line 55)
- Test: `klik_pos/tests/test_pos_extra_fields.py`

**Interfaces:**
- Consumes: nothing from prior tasks at runtime.
- Produces:
  - `_parse_extra_fields(data) -> dict` — pulls `extra_fields` out of the request payload (handles dict or JSON string), returns `{fieldname: value}`.
  - `_apply_extra_fields(doc, extra_fields) -> None` — sets each non-empty value whose fieldname exists on `doc`.

- [ ] **Step 1: Write the failing test**

Append to `klik_pos/tests/test_pos_extra_fields.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_extra_fields`
Expected: FAIL with `ImportError: cannot import name '_apply_extra_fields'`.

- [ ] **Step 3: Implement the helpers**

In `klik_pos/api/sales_invoice.py`, add directly below `_apply_walkin_party_fields` (after ~line 61). Note `json` is already imported at module top:

```python
def _parse_extra_fields(data):
	"""Extract the generic POS extra-fields map from a request payload."""
	if not isinstance(data, dict):
		return {}
	raw = data.get("extra_fields")
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except Exception:
			raw = {}
	return raw if isinstance(raw, dict) else {}


def _apply_extra_fields(doc, extra_fields):
	"""Set each configured POS extra field on the document, guarded.

	Only sets fields that exist on the doctype (``has_field``) and have a
	non-empty value, so a stray/removed fieldname is silently skipped.
	"""
	for fieldname, value in (extra_fields or {}).items():
		if value in (None, "") or not doc.meta.has_field(fieldname):
			continue
		doc.set(fieldname, value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_extra_fields`
Expected: PASS (12 tests total).

- [ ] **Step 5: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_pos/api/sales_invoice.py klik_pos/tests/test_pos_extra_fields.py
git commit -m "feat(pos): generic apply/parse helpers for POS extra fields"
```

---

## Task 3: Install child doctype + child-table field (idempotent) + client script

**Files:**
- Modify: `klik_pos/setup/pos_profile_fields.py`
- Test: `klik_pos/tests/test_pos_profile_fields.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ensure_pos_extra_fields_child()` and `install_pos_extra_fields_child()` (idempotent), called from the existing `ensure_pos_profile_feature_fields` after_migrate entrypoint. Creates child doctype `POS Extra Field` and a `Table` custom field `custom_pos_extra_fields` on POS Profile.

- [ ] **Step 1: Write the failing test**

Append to `klik_pos/tests/test_pos_profile_fields.py`:

```python
class TestPosExtraFieldsChild(FrappeTestCase):
    def test_child_doctype_and_table_field_exist_after_install(self):
        from klik_pos.setup.pos_profile_fields import install_pos_extra_fields_child
        import frappe

        install_pos_extra_fields_child()
        self.assertTrue(frappe.db.exists("DocType", "POS Extra Field"))
        self.assertTrue(frappe.db.has_column("POS Profile", "custom_pos_extra_fields"))

    def test_install_is_idempotent(self):
        from klik_pos.setup.pos_profile_fields import install_pos_extra_fields_child
        # second call must not raise
        install_pos_extra_fields_child()
        install_pos_extra_fields_child()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_profile_fields`
Expected: FAIL with `ImportError: cannot import name 'install_pos_extra_fields_child'`.

- [ ] **Step 3: Implement the child install**

Append to `klik_pos/setup/pos_profile_fields.py`:

```python
def install_pos_extra_fields_child():
    """Create the `POS Extra Field` child doctype and the `custom_pos_extra_fields`
    Table custom field on POS Profile. Idempotent and safe on every migrate."""
    if not frappe.db.exists("DocType", "POS Extra Field"):
        child = frappe.new_doc("DocType")
        child.update({
            "name": "POS Extra Field",
            "module": "KLiK PoS",
            "custom": 1,
            "istable": 1,
            "fields": [
                {
                    "fieldname": "so_si_commonfield",
                    "label": "SO/SI Common Field",
                    "fieldtype": "Select",
                    "description": "Common field in Sales Order / Sales Invoice",
                    "in_list_view": 1,
                    "reqd": 1,
                },
                {
                    "fieldname": "reqd",
                    "label": "Required",
                    "fieldtype": "Check",
                    "in_list_view": 1,
                    "default": "0",
                },
            ],
            "permissions": [],
        })
        child.insert(ignore_permissions=True)

    if not frappe.db.has_column("POS Profile", "custom_pos_extra_fields"):
        create_custom_fields({
            "POS Profile": [{
                "fieldname": "custom_pos_extra_fields",
                "label": "POS Extra Fields",
                "fieldtype": "Table",
                "options": "POS Extra Field",
                "insert_after": "allow_warehouse_change",
                "description": "Extra SO/SI common fields to capture in the POS Additional Info dialog.",
                "module": "KLiK PoS",
            }]
        }, update=True)


def ensure_pos_extra_fields_child():
    """Hook-safe wrapper. Never abort migrate on failure."""
    try:
        install_pos_extra_fields_child()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "klik_pos: POS Extra Field child install failed")
```

Then wire it into the existing entrypoint. Replace the body of `ensure_pos_profile_feature_fields` so both installers run:

```python
def ensure_pos_profile_feature_fields():
    """Hook entrypoint for after_migrate / after_install. Never abort on failure."""
    try:
        install_pos_profile_feature_fields()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "klik_pos: POS Profile feature-field install failed")
    ensure_pos_extra_fields_child()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_profile_fields`
Expected: PASS (all, including the 2 new tests).

- [ ] **Step 5: Run a migrate to confirm self-heal works end-to-end**

Run: `cd /home/kushal/frappe-bench && bench --site <site> migrate`
Expected: completes without error; `bench --site <site> console` → `frappe.db.has_column("POS Profile", "custom_pos_extra_fields")` returns `True`.

- [ ] **Step 6: Add the POS Profile client script to populate the Select**

Create the client script via console (or a fixture). Run `bench --site <site> console`:

```python
import frappe
if not frappe.db.exists("Client Script", "POS Profile - Extra Field Picker"):
    frappe.get_doc({
        "doctype": "Client Script",
        "name": "POS Profile - Extra Field Picker",
        "dt": "POS Profile",
        "view": "Form",
        "enabled": 1,
        "script": """
frappe.ui.form.on('POS Extra Field', {
    form_render(frm, cdt, cdn) {
        frappe.call({
            method: 'klik_pos.api.pos_profile.get_pos_extra_field_candidates',
            callback(r) {
                const opts = (r.message || []).map(f => ({ label: f.label, value: f.fieldname }));
                frappe.meta.get_docfield('POS Extra Field', 'so_si_commonfield', cdn).options = opts;
                frm.fields_dict.custom_pos_extra_fields.grid.refresh();
            }
        });
    }
});
"""
    }).insert(ignore_permissions=True)
    frappe.db.commit()
```

Expected: opening a POS Profile → "POS Extra Fields" grid → the SO/SI Common Field dropdown lists eligible fields by label.

- [ ] **Step 7: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_pos/setup/pos_profile_fields.py klik_pos/tests/test_pos_profile_fields.py
git commit -m "feat(pos): install POS Extra Field child table on POS Profile (idempotent)"
```

---

## Task 4: Required-field validation helper

**Files:**
- Modify: `klik_pos/api/pos_profile.py`
- Test: `klik_pos/tests/test_pos_extra_fields.py`

**Interfaces:**
- Consumes: active POS Profile (via existing `get_current_pos_profile`).
- Produces:
  - `get_required_extra_fieldnames(pos_profile=None) -> list[str]` — fieldnames marked `reqd` on the active profile's child table.
  - `validate_required_extra_fields(extra_fields, pos_profile=None) -> None` — raises `frappe.ValidationError` if any required field is empty/missing.

- [ ] **Step 1: Write the failing test**

Append to `klik_pos/tests/test_pos_extra_fields.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_extra_fields`
Expected: FAIL with `ImportError: cannot import name 'validate_required_extra_fields'`.

- [ ] **Step 3: Implement the validators**

Add to `klik_pos/api/pos_profile.py`:

```python
def get_required_extra_fieldnames(pos_profile=None):
    """Fieldnames marked Required on the active POS Profile's extra-field table."""
    if pos_profile is None:
        pos_profile = get_current_pos_profile()
    rows = getattr(pos_profile, "custom_pos_extra_fields", None) or []
    return [r.so_si_commonfield for r in rows if getattr(r, "reqd", 0) and r.so_si_commonfield]


def validate_required_extra_fields(extra_fields, pos_profile=None):
    """Raise if any Required configured extra field has no value."""
    extra_fields = extra_fields or {}
    missing = [
        fn for fn in get_required_extra_fieldnames(pos_profile)
        if extra_fields.get(fn) in (None, "")
    ]
    if missing:
        frappe.throw(
            _("Please fill required field(s): {0}").format(", ".join(missing))
        )
```

Add the import if not present at top of file (it already imports `from klik_pos.klik_pos.utils import get_current_pos_profile`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_extra_fields`
Expected: PASS (18 tests total).

- [ ] **Step 5: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_pos/api/pos_profile.py klik_pos/tests/test_pos_extra_fields.py
git commit -m "feat(pos): server-side required validation for POS extra fields"
```

---

## Task 5: Thread `extra_fields` through Sales Invoice; remove bespoke `po_no`

**Files:**
- Modify: `klik_pos/api/sales_invoice.py`

**Interfaces:**
- Consumes: `_parse_extra_fields`, `_apply_extra_fields` (Task 2); `validate_required_extra_fields` (Task 4).
- Produces: `build_sales_invoice_doc(..., extra_fields=None)` and `_update_existing_draft_invoice(..., extra_fields=None)` — `po_no` param removed from both.

- [ ] **Step 1: Locate every `po_no` site (gate)**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos && grep -n "po_no" klik_pos/api/sales_invoice.py`
Record every line. Each must be either removed or converted to flow via `extra_fields`. Known sites at plan time: ~968, ~1194, ~1403, ~1434, ~1457, ~1747, ~1769-1770, ~1853, ~1876, ~1890, ~4106, ~4120.

- [ ] **Step 2: Replace the `po_no` block in `build_sales_invoice_doc`**

In `build_sales_invoice_doc` signature (~1747) remove `po_no=None,` and add `extra_fields=None,` in its place. Replace the bespoke block (~1768-1770):

```python
	# Customer PO number (standard field; applies to any customer type)
	if po_no and doc.meta.has_field("po_no"):
		doc.po_no = po_no
```

with:

```python
	# Generic POS Profile extra fields (incl. po_no when configured)
	_apply_extra_fields(doc, extra_fields)
```

- [ ] **Step 3: Replace `po_no` in `_update_existing_draft_invoice`**

In its signature (~1853) remove `po_no=None,`, add `extra_fields=None,`. Replace its bespoke `po_no` assignment (~1876) with `_apply_extra_fields(doc, extra_fields)`. At ~1890, replace `invoice_doc.po_no = rebuilt_doc.po_no` — since `extra_fields` already applied to `rebuilt_doc`, copy generically. If the surrounding code copies specific fields from `rebuilt_doc`, add right after it:

```python
	for _ef in (_parse_extra_fields(data) if isinstance(data, dict) else {}):
		if invoice_doc.meta.has_field(_ef) and rebuilt_doc.meta.has_field(_ef):
			invoice_doc.set(_ef, rebuilt_doc.get(_ef))
```

(If `data` is not in scope at ~1890, pass `extra_fields` into that helper and iterate it instead — use whichever variable is in scope; the intent is to mirror the same fields the rebuild set.)

- [ ] **Step 4: Replace the call-site extraction + passing (hold path ~1401-1458)**

Replace:

```python
		walkin_name = data.get("walkin_name")
		walkin_phone = data.get("walkin_phone")
		po_no = data.get("po_no")
```

with:

```python
		walkin_name = data.get("walkin_name")
		walkin_phone = data.get("walkin_phone")
		extra_fields = _parse_extra_fields(data)
```

In both the `_update_existing_draft_invoice(...)` call (~1432-1434) and the `build_sales_invoice_doc(...)` call (~1455-1457), replace the `po_no=po_no,` argument with `extra_fields=extra_fields,`.

- [ ] **Step 5: Fix the remaining call sites (~966-968, ~1192-1194, ~4106, ~4120)**

For each call site found in Step 1 that passes `po_no=data.get("po_no")` (or similar) into `build_sales_invoice_doc` / `_update_existing_draft_invoice`, replace with `extra_fields=_parse_extra_fields(data)`. For `~4120` (`invoice_doc.po_no = rebuilt_doc.po_no` pattern) apply the same generic copy as Step 3. Re-run the Step 1 grep; the only remaining `po_no` references allowed are inside generic loops or comments.

- [ ] **Step 6: Add required-field enforcement on submit**

In `queue_sales_invoice` (the main submit entry; confirm name via `grep -n "def queue_sales_invoice" klik_pos/api/sales_invoice.py`), immediately after `data` is parsed to a dict, add:

```python
	from klik_pos.api.pos_profile import validate_required_extra_fields
	validate_required_extra_fields(_parse_extra_fields(data))
```

- [ ] **Step 7: Verify backend still imports and unit tests pass**

Run: `cd /home/kushal/frappe-bench && bench --site <site> run-tests --app klik_pos --module klik_pos.tests.test_pos_extra_fields`
Expected: PASS. Also: `cd /home/kushal/frappe-bench/apps/klik_pos && python -c "import ast; ast.parse(open('klik_pos/api/sales_invoice.py').read())"` → no output (syntax OK).

- [ ] **Step 8: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_pos/api/sales_invoice.py
git commit -m "refactor(pos): route SI fields through generic extra_fields; drop bespoke po_no"
```

---

## Task 6: Thread `extra_fields` through Sales Order (hold/resume); remove bespoke `po_no`

**Files:**
- Modify: `klik_pos/api/sales_order.py`

**Interfaces:**
- Consumes: `_apply_extra_fields`, `_parse_extra_fields` from `sales_invoice` (extend the existing import at lines 7-12).
- Produces: cart_meta carries `extra_fields`; SO build/rebuild apply them; resume payload returns them.

- [ ] **Step 1: Extend the import**

In `klik_pos/api/sales_order.py` (lines 7-12), add `_apply_extra_fields` and `_parse_extra_fields` to the existing `from klik_pos.api.sales_invoice import (...)` block.

- [ ] **Step 2: Put `extra_fields` into cart_meta; drop `po_no`**

In `_build_cart_meta` (~74-88), replace `"po_no": data.get("po_no") if isinstance(data, dict) else None,` with:

```python
        "extra_fields": _parse_extra_fields(data),
```

- [ ] **Step 3: Apply in `_build_sales_order_doc` (drop the `po_no` block)**

Replace (~113-114):

```python
    if cart_meta.get("po_no") and so.meta.has_field("po_no"):
        so.po_no = cart_meta.get("po_no")
```

with:

```python
    _apply_extra_fields(so, cart_meta.get("extra_fields"))
```

- [ ] **Step 4: Apply in `_rebuild_sales_order` (drop the `po_no` block)**

Replace (~148-149) the identical `po_no` block with the same `_apply_extra_fields(so, cart_meta.get("extra_fields"))` line.

- [ ] **Step 5: Return `extra_fields` on resume; drop `po_no`**

In `get_held_order_details` return dict (~262-264), replace `"po_no": cart_meta.get("po_no"),` with:

```python
            "extra_fields": cart_meta.get("extra_fields") or {},
```

- [ ] **Step 6: Enforce required fields when holding**

In `create_held_order`, after `data` is parsed to a dict (~178-179), add:

```python
        from klik_pos.api.pos_profile import validate_required_extra_fields
        validate_required_extra_fields(_parse_extra_fields(data))
```

- [ ] **Step 7: Verify**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos && grep -n "po_no" klik_pos/api/sales_order.py`
Expected: no matches. Then `python -c "import ast; ast.parse(open('klik_pos/api/sales_order.py').read())"` → no output.

- [ ] **Step 8: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_pos/api/sales_order.py
git commit -m "refactor(pos): route SO hold/resume through generic extra_fields; drop po_no"
```

---

## Task 7: cartStore — add `extraFields`, remove `poNo`

**Files:**
- Modify: `klik_spa/src/stores/cartStore.ts`

**Interfaces:**
- Produces: `extraFields: Record<string, string>`, `setExtraFields(v: Record<string, string>) => void`, `clearExtraFields()`. Removes `poNo`, `setPoNo`.

- [ ] **Step 1: Update the state interface (~114-138)**

Remove `poNo: string` and add `extraFields: Record<string, string>`. Remove `setPoNo: (v: string) => void` and add:

```ts
  setExtraFields: (v: Record<string, string>) => void
  clearExtraFields: () => void
```

- [ ] **Step 2: Update initial state + setters**

Replace `poNo: '',` (~153) with `extraFields: {},`. Replace `setPoNo: (v) => set({ poNo: v }),` (~455) with:

```ts
      setExtraFields: (v) => set({ extraFields: v }),
      clearExtraFields: () => set({ extraFields: {} }),
```

Find the clear-on-reset path that sets `poNo: ''` (~423, inside `clearCart`/reset) and replace it with `extraFields: {},`.

- [ ] **Step 3: Typecheck**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos/klik_spa && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "poNo|setPoNo|extraFields" || echo "no extraFields/poNo type errors"`
Expected: remaining `poNo` errors point to the consumer files handled in Tasks 9-11 (expected at this stage). The store itself must compile.

- [ ] **Step 4: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_spa/src/stores/cartStore.ts
git commit -m "feat(spa): cart store extraFields map; remove bespoke poNo"
```

---

## Task 8: `useExtraFields` hook + WalkinInfoModal dynamic fields

**Files:**
- Create: `klik_spa/src/hooks/useExtraFields.ts`
- Modify: `klik_spa/src/components/order/WalkinInfoModal.tsx`

**Interfaces:**
- Consumes: `posDetails.custom_pos_extra_fields` (configured rows) and the candidates endpoint.
- Produces:
  - `useExtraFields()` → `{ fields: ResolvedExtraField[] }` where `ResolvedExtraField = { fieldname: string; label: string; fieldtype: string; options: string[]; reqd: boolean }`.
  - `WalkinInfoModal` renders configured fields in a right column and returns `extraFields` in `onSave`.

- [ ] **Step 1: Create the hook**

Create `klik_spa/src/hooks/useExtraFields.ts`:

```ts
import { useEffect, useState } from "react";
import { usePOSProfileStore } from "../stores/posProfileStore";

export interface ResolvedExtraField {
  fieldname: string;
  label: string;
  fieldtype: string;
  options: string[];
  reqd: boolean;
}

interface Candidate { fieldname: string; label: string; fieldtype: string; options: string }

let _cache: Candidate[] | null = null;

export function useExtraFields(): { fields: ResolvedExtraField[] } {
  const posDetails = usePOSProfileStore((s) => s.posDetails);
  const [candidates, setCandidates] = useState<Candidate[]>(_cache || []);

  useEffect(() => {
    if (_cache) return;
    fetch("/api/method/klik_pos.api.pos_profile.get_pos_extra_field_candidates", {
      credentials: "include",
    })
      .then((r) => r.json())
      .then((d) => { _cache = d?.message || []; setCandidates(_cache); })
      .catch(() => setCandidates([]));
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rows: any[] = (posDetails as any)?.custom_pos_extra_fields || [];
  const byName = new Map(candidates.map((c) => [c.fieldname, c]));

  const fields: ResolvedExtraField[] = rows
    .map((row) => {
      const c = byName.get(row.so_si_commonfield);
      if (!c) return null;
      return {
        fieldname: c.fieldname,
        label: c.label,
        fieldtype: c.fieldtype,
        options: c.fieldtype === "Select" ? (c.options || "").split("\n").filter(Boolean) : [],
        reqd: !!row.reqd,
      } as ResolvedExtraField;
    })
    .filter((f): f is ResolvedExtraField => f !== null);

  return { fields };
}
```

(Confirm the POS profile store path/selector via `grep -rn "usePOSProfileStore" klik_spa/src/stores`; adjust the import if the file name differs.)

- [ ] **Step 2: Update WalkinInfoModal props + render**

Rewrite `klik_spa/src/components/order/WalkinInfoModal.tsx` so it (a) drops the PO input, (b) takes `extraFields`/returns them, (c) renders a right column of configured controls. Full file:

```tsx
import { useState } from "react";
import { X } from "lucide-react";
import type { WalkinDetails } from "../../stores/cartStore";
import { useExtraFields } from "../../hooks/useExtraFields";

interface Props {
  isWalkin: boolean;
  initial: WalkinDetails;
  masterDisplay: WalkinDetails;
  extraFields: Record<string, string>;
  onClose: () => void;
  onSave: (d: WalkinDetails & { extraFields: Record<string, string> }) => void;
}

export default function WalkinInfoModal({ isWalkin, initial, masterDisplay, extraFields, onClose, onSave }: Props) {
  const seed = isWalkin ? initial : masterDisplay;
  const [name, setName] = useState(seed.name);
  const [taxId, setTaxId] = useState(seed.taxId);
  const [phone, setPhone] = useState(seed.phone);
  const { fields } = useExtraFields();
  const [extra, setExtra] = useState<Record<string, string>>(extraFields || {});

  const base =
    "w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-beveren-500";
  const ro = "opacity-60 cursor-not-allowed bg-gray-100 dark:bg-gray-800";

  const setField = (fn: string, v: string) => setExtra((p) => ({ ...p, [fn]: v }));
  const missingRequired = fields.some((f) => f.reqd && !(extra[f.fieldname] || "").trim());

  const renderControl = (f: ReturnType<typeof useExtraFields>["fields"][number]) => {
    const val = extra[f.fieldname] ?? "";
    if (f.fieldtype === "Select") {
      return (
        <select className={base} value={val} onChange={(e) => setField(f.fieldname, e.target.value)}>
          <option value="">—</option>
          {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    }
    if (f.fieldtype === "Check") {
      return (
        <input type="checkbox" checked={val === "1"} onChange={(e) => setField(f.fieldname, e.target.checked ? "1" : "0")} />
      );
    }
    const inputType = f.fieldtype === "Int" || f.fieldtype === "Float" ? "number"
      : f.fieldtype === "Date" ? "date" : "text";
    return (
      <input className={base} type={inputType} value={val} onChange={(e) => setField(f.fieldname, e.target.value)} />
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className={`w-full ${fields.length ? "max-w-2xl" : "max-w-sm"} rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl`}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white">Additional Info</h3>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded">
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>
        {!isWalkin && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
            Name, Tax ID and Phone come from the customer record and are read-only.
          </p>
        )}
        <div className={`grid gap-x-6 gap-y-3 ${fields.length ? "grid-cols-2" : "grid-cols-1"}`}>
          <div className="space-y-3">
            <div>
              <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Name</label>
              <input className={`${base} ${isWalkin ? "" : ro}`} value={name} readOnly={!isWalkin}
                onChange={(e) => setName(e.target.value)} placeholder="Customer name for this sale" />
            </div>
            <div>
              <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Tax ID</label>
              <input className={`${base} uppercase tracking-widest ${isWalkin ? "" : ro}`} value={taxId} readOnly={!isWalkin}
                onChange={(e) => setTaxId(e.target.value.toUpperCase())} placeholder="A123456789P" />
            </div>
            <div>
              <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Phone</label>
              <input className={`${base} ${isWalkin ? "" : ro}`} value={phone} readOnly={!isWalkin} type="tel"
                onChange={(e) => setPhone(e.target.value)} placeholder="Phone for this sale" />
            </div>
          </div>
          {fields.length > 0 && (
            <div className="space-y-3">
              {fields.map((f) => (
                <div key={f.fieldname}>
                  <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">
                    {f.label}{f.reqd && <span className="text-red-500"> *</span>}
                  </label>
                  {renderControl(f)}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200">Cancel</button>
          <button
            disabled={missingRequired}
            onClick={() => onSave({ name: name.trim(), taxId: taxId.trim(), phone: phone.trim(), extraFields: extra })}
            className="px-3 py-2 text-sm rounded-lg bg-beveren-600 text-white hover:bg-beveren-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck (consumer wiring lands in Task 9)**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos/klik_spa && npx tsc --noEmit -p tsconfig.json 2>&1 | grep "WalkinInfoModal" || echo "modal compiles; remaining errors are in CustomerSearchSection (Task 9)"`
Expected: the only references to the old `poNo` prop are in `CustomerSearchSection.tsx` (handled next).

- [ ] **Step 4: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_spa/src/hooks/useExtraFields.ts klik_spa/src/components/order/WalkinInfoModal.tsx
git commit -m "feat(spa): dynamic configured extra fields in Additional Info modal"
```

---

## Task 9: CustomerSearchSection — wire extraFields, generalize chips, F4

**Files:**
- Modify: `klik_spa/src/components/order/CustomerSearchSection.tsx`

**Interfaces:**
- Consumes: `extraFields`/`setExtraFields` (Task 7), `useExtraFields` (Task 8), updated `WalkinInfoModal` props (Task 8).

- [ ] **Step 1: Swap the store selectors**

Replace `const poNo = useCartStore((state) => state.poNo);` (~79) with:

```tsx
  const extraFields = useCartStore((state) => state.extraFields);
  const setExtraFields = useCartStore((state) => state.setExtraFields);
  const { fields: extraFieldDefs } = useExtraFields();
```

Add `import { useExtraFields } from "../../hooks/useExtraFields";` to the imports. Remove any `setPoNo` destructure if present.

- [ ] **Step 2: Replace the PO chip with generic chips (~639-643)**

Replace:

```tsx
                    {poNo && (
                      <div className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                        <span className="font-mono">PO: {poNo}</span>
                      </div>
                    )}
```

with:

```tsx
                    {extraFieldDefs
                      .filter((f) => (extraFields[f.fieldname] || "").toString().trim())
                      .map((f) => (
                        <div key={f.fieldname} className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                          <span className="font-mono">{f.label}: {extraFields[f.fieldname]}</span>
                        </div>
                      ))}
```

- [ ] **Step 3: Update the WalkinInfoModal usage (~737-756)**

Replace the `poNo={poNo}` prop and the `onSave` body:

```tsx
          extraFields={extraFields}
          onClose={() => setShowWalkinModal(false)}
          onSave={(d) => {
            if (selectedCustomer.isWalkin === 1) {
              setWalkinDetails({ name: d.name, taxId: d.taxId, phone: d.phone });
            }
            setExtraFields(d.extraFields);
            setShowWalkinModal(false);
          }}
```

- [ ] **Step 4: Add the F4 listener (open the modal)**

First confirm F4 is unbound: `cd /home/kushal/frappe-bench/apps/klik_pos/klik_spa && grep -rn "F4" src/`. If it returns matches other than KeyboardShortcutsPanel, STOP and report the conflict instead of overriding.

Add inside the component body (after the existing hooks), guarded against typing context:

```tsx
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "F4") return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (!selectedCustomer) return;
      e.preventDefault();
      setShowWalkinModal(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedCustomer]);
```

(Ensure `useEffect` is imported from React in this file; add it if missing.)

- [ ] **Step 5: Typecheck the SPA fully**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos/klik_spa && npx tsc --noEmit -p tsconfig.json 2>&1 | head -30`
Expected: errors now only in `PaymentDialog.tsx` and `heldOrderToCart.ts` (Tasks 10-11). No errors in CustomerSearchSection/WalkinInfoModal/cartStore.

- [ ] **Step 6: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_spa/src/components/order/CustomerSearchSection.tsx
git commit -m "feat(spa): wire extra fields + generic chips + F4 in customer section"
```

---

## Task 10: PaymentDialog — send `extra_fields`, remove `po_no`

**Files:**
- Modify: `klik_spa/src/components/dialog/PaymentDialog.tsx`

**Interfaces:**
- Consumes: `extraFields` from cart store.

- [ ] **Step 1: Update the store destructure (~199)**

Replace `const { clearCart, walkinDetails, setWalkinDetails, poNo } = useCartStore();` with:

```tsx
  const { clearCart, walkinDetails, setWalkinDetails, extraFields } = useCartStore();
```

- [ ] **Step 2: Replace both payload keys (~645 and ~1438)**

Replace each `po_no: poNo || null,` with:

```tsx
        extra_fields: extraFields,
```

(Confirm both occurrences via `grep -n "po_no\|poNo" src/components/dialog/PaymentDialog.tsx` and replace all.)

- [ ] **Step 3: Typecheck**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos/klik_spa && npx tsc --noEmit -p tsconfig.json 2>&1 | grep "PaymentDialog" || echo "PaymentDialog OK"`
Expected: no PaymentDialog errors.

- [ ] **Step 4: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_spa/src/components/dialog/PaymentDialog.tsx
git commit -m "feat(spa): send generic extra_fields from payment payloads"
```

---

## Task 11: heldOrderToCart — restore `extraFields`, remove `setPoNo`

**Files:**
- Modify: `klik_spa/src/utils/heldOrderToCart.ts`

**Interfaces:**
- Consumes: `setExtraFields` (Task 7); `extra_fields` from `get_held_order_details` (Task 6).

- [ ] **Step 1: Replace the restore line (~73)**

Replace `useCartStore.getState().setPoNo(od.po_no || od.cart_meta?.po_no || '');` with:

```ts
  useCartStore.getState().setExtraFields(od.extra_fields || od.cart_meta?.extra_fields || {});
```

- [ ] **Step 2: Full SPA typecheck (should be clean now)**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos/klik_spa && npx tsc --noEmit -p tsconfig.json && echo "TSC CLEAN"`
Expected: `TSC CLEAN` (no remaining `poNo`/`setPoNo` references anywhere).
Cross-check: `grep -rn "poNo\|setPoNo\|po_no" src/ | grep -v "extra_fields"` → no matches.

- [ ] **Step 3: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_spa/src/utils/heldOrderToCart.ts
git commit -m "feat(spa): restore extra_fields on held-order resume"
```

---

## Task 12: KeyboardShortcutsPanel — document F4

**Files:**
- Modify: `klik_spa/src/components/KeyboardShortcutsPanel.tsx`

- [ ] **Step 1: Add F4 to the Order group**

In the `Order` group's `shortcuts` array, add as the first entry:

```tsx
      { keys: ["F4"], description: "Open Additional Info" },
```

- [ ] **Step 2: Typecheck**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos/klik_spa && npx tsc --noEmit -p tsconfig.json && echo "TSC CLEAN"`
Expected: `TSC CLEAN`.

- [ ] **Step 3: Commit**

```bash
cd /home/kushal/frappe-bench/apps/klik_pos
git add klik_spa/src/components/KeyboardShortcutsPanel.tsx
git commit -m "docs(spa): add F4 (Additional Info) to keyboard shortcuts panel"
```

---

## Task 13: End-to-end manual verification + SPA build

**Files:** none (verification only).

- [ ] **Step 1: Build the SPA**

Run: `cd /home/kushal/frappe-bench/apps/klik_pos/klik_spa && npm run build`
Expected: build succeeds (chunk-size warning is pre-existing and acceptable).

- [ ] **Step 2: Configure a test field**

In ERPNext: create a custom Select field `delivery_method` (options e.g. `Pickup\nCourier\nDine-in`) on **both** Sales Order and Sales Invoice. In a POS Profile, add two rows to "POS Extra Fields": `delivery_method` (Required ✔) and `territory`.

- [ ] **Step 3: Cashier flow**

In POS: select a customer, press **F4** → Additional Info opens with a right column showing Delivery Method (required) and Territory. Confirm Save is blocked until Delivery Method is set. Set both; confirm chips appear in the customer summary.

- [ ] **Step 4: Checkout + hold/resume**

Complete a checkout → open the resulting Sales Invoice → confirm `delivery_method` and `territory` are set. Then hold an order with values set, resume it → confirm the modal re-shows the values and the held Sales Order carries them.

- [ ] **Step 5: Required enforcement (server)**

Attempt a checkout with the required field empty by editing client state / using an API client → expect the server to reject with "Please fill required field(s): delivery_method".

- [ ] **Step 6: Regression — PO via config**

Add a `po_no` row to the profile, set a value, checkout → confirm `po_no` lands on the Sales Invoice (proving the bespoke removal is fully covered by the generic path).

---

## Self-Review

**Spec coverage:**
- Child table config → Task 3. Eligibility endpoint (name+type intersection, whitelist, exclusions) → Task 1. Config delivery to SPA (`as_dict` + candidates endpoint) → Tasks 1/8. Modal two-column dynamic render → Task 8. Cart state `extraFields` → Task 7. Write-through `_apply_extra_fields` (SI + SO) → Tasks 2/5/6. Held-order resume → Tasks 6/11. Required (client + server) → Tasks 4/8/5/6. Remove bespoke `po_no` → Tasks 5/6/7/9/10/11. Generalized chip → Task 9. F4 + shortcuts panel → Tasks 9/12. Tests → Tasks 1-6. Migration notes (PO opt-in, not on receipt) → covered by Task 6/13. All spec sections map to a task.

**Placeholder scan:** No TBD/TODO; every code step shows real code. The only intentionally site-dependent steps (Task 5 Steps 3/5, the `~line` numbers) are gated by a grep-first step so the implementer resolves exact locations — code to write is fully specified.

**Type consistency:** `extra_fields` (backend snake_case) ↔ `extraFields` (frontend camelCase) consistent throughout. `_apply_extra_fields(doc, extra_fields)` / `_parse_extra_fields(data)` / `validate_required_extra_fields(extra_fields, pos_profile=None)` signatures match across Tasks 2/4/5/6. `ResolvedExtraField` shape consistent between hook (Task 8) and modal/section (Tasks 8/9). `setExtraFields(v: Record<string,string>)` consistent across Tasks 7/9/10/11.

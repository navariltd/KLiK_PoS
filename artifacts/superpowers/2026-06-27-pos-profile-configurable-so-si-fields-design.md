# Design: Configurable SO/SI Common Fields in POS Profile

**Date:** 2026-06-27
**Status:** Approved (pending final spec review)
**Area:** klik_pos — POS Profile config, Additional Info modal, Sales Order / Sales Invoice builders

## Problem

Different deployments need to capture different extra order attributes at the
point of sale — e.g. a custom "Delivery Method" Select, or an existing field
like `territory`. We do not want to hardcode each one. We want POS Profile to
let an admin pick **any field common to both Sales Order and Sales Invoice** and
have it appear in the cashier-facing Additional Info dialog, write through to the
resulting document, and survive hold/resume.

Not every instance needs this, and some instances have their own custom field
requirements. The mechanism must be fully generic and opt-in per profile.

## Goals

- Admin configures, per POS Profile, a list of SO∩SI common fields to surface.
- Those fields render in the existing Additional Info modal (both walk-in and
  regular-customer modes), in a second column.
- Selected values write through to both Sales Order (held) and Sales Invoice
  (checkout) generically.
- Values persist on held Sales Orders and restore on resume.
- A field can be marked Required; enforced client-side and server-side.
- Retire the recently-added bespoke `po_no` handling — it is a native SO/SI
  common field and is fully subsumed by this generic mechanism.

## Non-Goals (YAGNI)

- Per-doctype targeting (the "common to both" rule removes the need).
- Complex fieldtypes: Table, Table MultiSelect, Currency totals, Text Editor,
  Attach, etc.
- Conditional visibility / dependent fields.
- A label override per field (use the field's own label).

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Where fields render | Existing Additional Info modal, **both** modes |
| Picker mechanism | **Computed Select** populated at runtime via a whitelisted endpoint + POS Profile client script |
| Eligible fields | Intersection of SO and SI fields by **fieldname + fieldtype**, filtered to a **safe-fieldtype whitelist** |
| Multiple fields | **Child table** on POS Profile |
| Required toggle | **Yes**, per row |
| Label override | **Removed** — use field's own label |
| Modal layout | Existing fields left column; configured extra fields **right column** |
| Bespoke `po_no` | **Removed**; flows through the generic mechanism instead |
| Value-set indicator | Generalize the existing "PO: xxx" chip so any set extra field shows a chip |

### Safe-fieldtype whitelist

`Select, Link, Data, Small Text, Int, Float, Check, Date`

Excluded: anything `hidden`, `read_only`, standard system fields
(`name`, `owner`, `docstatus`, `idx`, amount/total fields), and any fieldtype
outside the whitelist.

## Architecture

### 1. Configuration — POS Profile child table

New child doctype (e.g. `POS Extra Field`) on a new child-table custom field
`custom_pos_extra_fields` on POS Profile. Row schema:

| Column | Type | Notes |
|---|---|---|
| `so_si_commonfield` | Select | Computed dropdown of SO∩SI common fields |
| `reqd` | Check | Cashier must set a value before save/checkout |

The Select's options are populated by a **POS Profile client script** that calls
the eligibility endpoint and sets the field's options via `set_df_property` (or
equivalent), keeping it fresh without re-migration.

The child doctype + child-table custom field are installed idempotently via the
existing self-healing setup path (`klik_pos/setup/pos_profile_fields.py` /
`ensure_pos_profile_feature_fields` after_migrate hook), following the same
pattern as `allow_warehouse_change`.

### 2. Eligibility endpoint

New whitelisted function in `klik_pos/api/pos_profile.py`:

```python
@frappe.whitelist()
def get_pos_extra_field_candidates():
    """Return SO∩SI common fields eligible for POS Profile extra-field config."""
```

Logic:
1. `so = {f.fieldname: f for f in frappe.get_meta("Sales Order").fields}`
2. `si = {f.fieldname: f for f in frappe.get_meta("Sales Invoice").fields}`
3. Intersect by fieldname **and** matching `fieldtype`.
4. Keep only whitelisted fieldtypes; drop `hidden`, `read_only`, and standard
   system/amount fields.
5. Return `[{fieldname, label, fieldtype, options}]` (options as the raw Select
   option string or the Link target doctype).

This endpoint serves both the POS Profile client script (to populate the Select)
and the SPA (to resolve each configured field's fieldtype/options for rendering).

### 3. Config delivery to SPA

`get_pos_details()` already returns `pos.as_dict()`, so `custom_pos_extra_fields`
arrives with no extra work. The SPA additionally calls
`get_pos_extra_field_candidates()` once (cached for the session) to resolve
fieldtype/options for each configured row.

### 4. Modal rendering — `WalkinInfoModal.tsx`

- Modal widens from `max-w-sm` to a responsive **two-column** layout.
- Left column: existing Name / Tax ID / Phone (unchanged behavior, read-only for
  non-walk-in).
- Right column: one control per configured field, rendered by fieldtype:
  - **Select** → dropdown of its options
  - **Link** → link picker (reuse existing search infra)
  - **Data / Small Text** → text input
  - **Int / Float** → number input
  - **Check** → checkbox
  - **Date** → date input
- `reqd` fields show a required marker and **block Save** until filled.
- The bespoke PO No. input is **removed** (PO is now an optional configured
  field).

### 5. Cart state — `cartStore.ts`

- Add `extraFields: Record<string, string | number | boolean>` to cart state,
  alongside the existing walk-in details, with setter `setExtraFields`.
- **Remove** the bespoke `poNo` state and `setPoNo`.

### 6. Write-through — `sales_order.py` / `sales_invoice.py`

Add a shared helper mirroring the existing `po_no` guard pattern
(`sales_invoice.py:1769`, `sales_order.py:113`):

```python
def _apply_extra_fields(doc, extra_fields):
    for fieldname, value in (extra_fields or {}).items():
        if value not in (None, "") and doc.meta.has_field(fieldname):
            doc.set(fieldname, value)
```

- `extra_fields` is threaded through `cart_meta` → builders the same way
  `walkin_name` / `po_no` are today (build paths around `sales_invoice.py:966`,
  `:1192`, `:1432`, `:1766`; `sales_order.py:85`, `:113`, `:148`, `:262`).
- The `doc.meta.has_field` guard means a fieldname not present on the target
  document is silently skipped — never errors.
- **Remove** all bespoke `po_no` params/assignments now that it flows through
  `extra_fields`.

### 7. Held-order resume

Because configured fields are real Sales Order fields, they persist naturally on
the held SO. On resume (`heldOrderToCart.ts`), read them back into `extraFields`
the same place `po_no`/walk-in details are restored today
(`heldOrderToCart.ts:73`). Remove the bespoke `setPoNo` restore line.

### 8. Required enforcement (server-side)

At checkout/hold, before building the document, validate that every configured
`reqd` field on the active POS Profile has a non-empty value in `extra_fields`.
Reject with a clear message if missing. This makes the rule unbypassable via
client-state edits.

### 9. Value-set chip — `CustomerSearchSection.tsx`

Generalize the existing bespoke "PO: xxx" chip (`CustomerSearchSection.tsx:639`):
instead of a hardcoded PO chip, render one chip per **set** extra field
(`<field label>: <value>`). This preserves the PO-chip behavior generically and
gives every configured field a "value is set" indicator.

### 10. F4 shortcut

There is **no central keyboard map**; shortcuts live per-component. Add a
page-level `keydown` listener (where the Additional Info button lives, near
`CustomerSearchSection.tsx`) that opens the Additional Info modal on **F4**,
guarded so it does not fire while typing in an input/textarea/select.

- Verify F4 is currently unbound before claiming it; if taken, flag rather than
  override.
- Add the F4 entry to `KeyboardShortcutsPanel.tsx` (Order group) so it is
  discoverable.

## Data Flow

```
POS Profile (custom_pos_extra_fields rows)
        │  as_dict()
        ▼
get_pos_details() ──► SPA pos_details
        │                    │
        │   get_pos_extra_field_candidates() (cached)
        │                    ▼
        │            resolve fieldtype/options
        ▼                    ▼
   WalkinInfoModal renders right-column controls
        │  onSave
        ▼
   cartStore.extraFields {fieldname: value}
        │  cart_meta payload (checkout / hold)
        ▼
   sales_invoice.py / sales_order.py
        │  _apply_extra_fields(doc, extra_fields)  [has_field guard]
        ▼
   Sales Invoice / Sales Order document
        ▲
        │  on resume: heldOrderToCart reads fields back into extraFields
```

## Testing

**Backend (pytest):**
- `get_pos_extra_field_candidates`: returns SO∩SI intersection; respects
  fieldname+fieldtype match; applies whitelist; excludes hidden/read_only/system
  fields.
- `_apply_extra_fields`: sets valid fields; skips fields not on the doc
  (`has_field` false); skips empty/None values.
- Extend SO/SI builder tests: configured extra field lands on the built document.
- Required enforcement: checkout rejects a missing required value.

**Frontend:**
- Modal renders the correct control per fieldtype.
- Required field blocks Save when empty.
- Resume restores extra-field values into the modal.

**Manual verification:**
- Configure a Select "Delivery Method" custom field on SO & SI; add it (and
  `territory`, and `po_no`) to a POS Profile; confirm render, write-through on
  both hold and checkout, resume, chip display, and F4 opening the modal.

## Migration / Backward-Compatibility Notes

- **PO No. becomes opt-in.** It is no longer always shown; an instance that wants
  it adds a `po_no` row to the child table. Acceptable: `po_no` POS support was
  added recently and is not entrenched.
- `po_no` is **not** on the thermal receipt, so removing bespoke handling does
  not affect printed output (verified via repo grep, 2026-06-27).
- Child doctype/field install is idempotent and self-healing via the existing
  after_migrate path.

## Files Touched (anticipated)

- `klik_pos/setup/pos_profile_fields.py` — install child-table field + child doctype
- `klik_pos/api/pos_profile.py` — `get_pos_extra_field_candidates`, ship config
- `klik_pos/api/sales_order.py` — `_apply_extra_fields`, thread `extra_fields`, remove `po_no`
- `klik_pos/api/sales_invoice.py` — `_apply_extra_fields`, thread `extra_fields`, remove `po_no`
- `klik_spa/src/stores/cartStore.ts` — `extraFields` state, remove `poNo`
- `klik_spa/src/components/order/WalkinInfoModal.tsx` — two-column layout, dynamic fields, remove PO input
- `klik_spa/src/components/order/CustomerSearchSection.tsx` — generic chips, F4 listener, remove PO chip
- `klik_spa/src/components/dialog/PaymentDialog.tsx` — send `extra_fields`, remove `po_no`
- `klik_spa/src/utils/heldOrderToCart.ts` — restore `extraFields`, remove `setPoNo`
- `klik_spa/src/components/KeyboardShortcutsPanel.tsx` — F4 entry
- POS Profile client script — populate `so_si_commonfield` Select options
- Tests under `klik_pos/tests/`

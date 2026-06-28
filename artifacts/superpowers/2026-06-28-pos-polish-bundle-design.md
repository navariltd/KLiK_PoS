# Design: POS Polish Bundle (ESC New-Order, Link-field picker, hold-naming placement)

**Date:** 2026-06-28
**Status:** Approved (pending spec review)
**Area:** klik_pos — PaymentDialog, Additional Info modal / extra-fields, POS Profile customization

Three small, independent changes bundled into one spec/plan.

---

## A. ESC on Payment Dialog

**Current:** PaymentDialog has a keydown effect handling `F10` (complete payment). Its completed/success state renders a "Start New Order" button calling `finalizeCompletedOrderState(() => onClose(true))`.

**Goal:** Escape should do the contextual default:
- **Completed state** → trigger the same action as "Start New Order": `finalizeCompletedOrderState(() => onClose(true))`.
- **Payment-entry state** → close the dialog (the same path as the header X / normal close).

**Approach:** Extend the existing `isOpen` keydown effect with an `Escape` branch that checks the completed-state flag (the same condition that renders the success branch) and dispatches accordingly. One handler, two outcomes. Guard so Escape inside a focused input still resolves to the dialog action (standard behavior). Confirm there is no pre-existing backdrop/ESC close that would double-fire; if one exists, consolidate to this handler.

**Testing:** No JS test runner — verify via `npm run build` + manual: ESC during payment closes; ESC on the completed screen starts a new order.

---

## B. Link-type extra fields → searchable typeahead

**Current:** In `WalkinInfoModal.renderControl`, `Link` fields fall through to a plain text input. The eligibility endpoint returns each field's `fieldtype` and raw `options`; for `Link`, `options` is the target doctype (e.g. `Contact`). `useExtraFields` currently only splits `options` for `Select`.

**Goal:** Render Link extra-fields (e.g. Contact Person → Contact) as a searchable picker.

**Approach:**
- Extend `ResolvedExtraField` with `linkDoctype: string` (set from the candidate's `options` when `fieldtype === "Link"`).
- `renderControl` gains a `Link` branch rendering the existing `AutoComplete` component (`klik_spa/src/components/ui/AutoComplete.tsx`, which already supports an async `onSearch`). `onSearch(txt)` calls a new endpoint and maps results to `{value, label}`. The stored `extraFields[fieldname]` value is the selected record `name` (string).
- **New whitelisted endpoint** `search_extra_field_link(doctype, txt, page_length=10)` in `klik_pos/api/pos_profile.py`:
  - **Security:** validate `doctype` is one of the Link targets among the *eligible* extra-field candidates (`{f["options"] for f in _eligible_common_fields() if f["fieldtype"]=="Link"}`). Reject anything else — no arbitrary doctype enumeration.
  - Search by `name` / `title` field with `txt` (LIKE), respecting standard read permissions; return `[{value: name, label}]`.

**Write-through:** unchanged — the generic allow-listed `_apply_extra_fields` already sets the Link field to the stored `name`.

**Testing:** backend unit test for `search_extra_field_link` doctype validation (rejects a non-eligible doctype; accepts an eligible Link target). Frontend: `npm run build` + manual (type to search Contact, pick, value persists, lands on SO/SI).

---

## C. Move `custom_hold_naming` into "Sales & Checkout Permissions"

**Current:** `custom_hold_naming` is a Check field ("When enabled, held POS invoices get a HOLD- naming series; renamed on submit"), created directly on the site (NOT in klik_pos code), `insert_after: naming_series`. The user wants klik_pos to manage it and place it in the Sales & Checkout Permissions section.

**Approach:**
- **Codify it as a klik_pos-managed POS Profile field** in `klik_pos/klik_pos/custom/pos_profile.json` (the auto-synced Customize-Form customization), with:
  - fieldname `custom_hold_naming`, label "Custom Hold Naming", fieldtype Check, the existing description, `insert_after: custom_sales_person_pin_required` (inside the `custom_sales__checkout_permissions` section).
- **Migrate the live field:** update the existing Custom Field's `insert_after` to `custom_sales_person_pin_required` and clear POS Profile cache, so it moves immediately on this instance (the json keeps it placed on fresh installs / other instances).

**Testing:** confirm via console that `insert_after` is updated and the field renders in the Sales & Checkout Permissions section after a POS Profile reload; `bench migrate` stays green.

---

## Files Touched (anticipated)
- `klik_spa/src/components/dialog/PaymentDialog.tsx` — ESC handler (A).
- `klik_spa/src/hooks/useExtraFields.ts` — expose `linkDoctype` (B).
- `klik_spa/src/components/order/WalkinInfoModal.tsx` — Link branch in `renderControl` (B).
- `klik_pos/api/pos_profile.py` — `search_extra_field_link` endpoint (B).
- `klik_pos/klik_pos/custom/pos_profile.json` — manage + place `custom_hold_naming` (C).
- `klik_pos/tests/test_pos_extra_fields.py` — endpoint validation test (B).

## Out of scope (YAGNI)
- Link picker create-new / advanced filters.
- Changing hold-naming behavior itself (only its form placement/management).

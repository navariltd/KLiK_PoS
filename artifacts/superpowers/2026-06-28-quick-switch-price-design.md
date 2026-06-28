# Design: Quick Switch Price (per-cart-line price-list pills)

**Date:** 2026-06-28
**Status:** Approved (pending spec review)
**Area:** klik_pos — POS Profile setting, item pricing API, cart line UI, cart store

## Problem
Cashiers want to see, per cart line, the item's rate across the available selling price lists and switch the line's rate with one tap — without changing the whole-cart price list.

## Decisions (from brainstorming)
- New POS Profile checkbox **`custom_quick_switch_price`** in the **"Item & Search Behaviors"** section.
- Pills show price lists from the **same allowed set as the existing switcher** (`get_selling_price_lists`).
- Pills render **on cart line items** (not product tiles). Clicking re-prices **that cart line**.
- Pill label = **rate + price-list name truncated to 8 chars** (e.g. `Retail   12.50`).

## Architecture

> **Implementation note (reconciled):** Most of the needed infrastructure already
> existed for the `allow_price_list_switching` per-line dropdown. The build reuses
> it — **no new endpoint and no cart-store changes were required**, beyond the new
> config checkbox and the pills UI.

### 1. Config
Add `custom_quick_switch_price` (Check) to `klik_pos/klik_pos/custom/pos_profile.json` in the Item & Search Behaviors section. It flows to the SPA via `get_pos_details()` → `posDetails.custom_quick_switch_price` (also added to the `POSProfile` TS type).

### 2. Per-item rates — reuse existing data (no new endpoint)
`CartItemRow` already fetches `get_full_pricing_and_batch_details`, whose response includes `price_lists: [{price_list, rate, uom}]` (the item's rate per price list). The pills consume `fullItemData.price_lists` directly. The fetch effect, previously gated on `isExpanded`, now also fires when `custom_quick_switch_price` is enabled so the data is present for the always-visible pills.

### 3. Reprice — reuse existing handler (no cart-store changes)
Clicking a pill calls the existing `handleLinePriceListChange(price_list)`, which records `selectedPriceList` (via `onDiscountChange`) and applies the rate through the existing `onCustomRateChange(item, rate, false)` path. Highlight = `itemDiscount.selectedPriceList === price_list`.

### 4. Cart line UI (`CartItemRow`)
When `custom_quick_switch_price` is enabled and `fullItemData.price_lists` is non-empty, render a single horizontal row of compact pills beneath Row 2 (rate/qty/total):
- Label: `<name truncated to 8 chars> <rate>` (rate via `formatCurrencyWithSymbol`); full name+rate in the `title` tooltip.
- Pills filtered to the line's UOM (matching the existing dropdown).
- The active price list's pill is highlighted (beveren styling).
- Compact styling, `overflow-x-auto`, no wrap, so ~5 pills fit; click uses `stopPropagation` so it doesn't toggle row expansion.

### 5. Coexistence
Independent of `allow_price_list_switching` (whole-cart switch / expanded-row dropdown). A pill click is a per-line override; existing discount/tax logic recalculates on the new base rate.

## Testing
- **Frontend:** `npm run build` gate (tsc unusable in this repo); manual — enable the setting, add an item, see pills, click one, confirm the line rate and totals update and the pill highlights. Reprice path is the already-exercised `handleLinePriceListChange`/`onCustomRateChange`.
- **Backend:** none added — reuses the existing `get_full_pricing_and_batch_details` data, already covered by the price-list-switching feature.

## Files Touched (actual)
- `klik_pos/klik_pos/custom/pos_profile.json` — `custom_quick_switch_price` checkbox.
- `klik_spa/src/stores/posProfileStore.ts` — `custom_quick_switch_price` on the `POSProfile` type.
- `klik_spa/src/components/order/CartItemRow.tsx` — pill row + fetch-when-enabled + reuse of `handleLinePriceListChange`.

## Out of scope (YAGNI)
- Pills on product tiles / product browser (cart-line only for now).
- Persisting per-line price-list choice across hold/resume (can be added later if needed).
- Bulk prefetch of all visible products' rates.

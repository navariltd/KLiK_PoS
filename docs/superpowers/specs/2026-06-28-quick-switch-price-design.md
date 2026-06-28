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

### 1. Config
Add `custom_quick_switch_price` (Check) to `klik_pos/klik_pos/custom/pos_profile.json` in the Item & Search Behaviors section. It flows to the SPA via `get_pos_details()` → `posDetails.custom_quick_switch_price`.

### 2. Endpoint — rates across price lists for an item
New whitelisted function in `klik_pos/api/item/item_price.py`:
```
get_item_prices_across_price_lists(item_code, uom=None, customer=None) -> list[dict]
```
- Resolve the allowed selling price lists (same source as `get_selling_price_lists`).
- For each, query the item's `price_list_rate` (reuse existing `fetch_item_price`/Item Price query logic, honoring UOM conversion as the current pricing does).
- Return `[{price_list, rate, currency}]`, omitting price lists with no rate for the item.
- Keep it per-item (cart is small); the SPA calls it per cart line lazily.

### 3. Cart store
The line already carries `price` and `original_price`. Add tracking of the active price list per line so the correct pill highlights:
- Add `selectedPriceList?: string` to the cart item shape (per-line), set when a pill is clicked.
- Add a store action `setItemRate(id: string, rate: number, priceList: string)` that updates the line's `price` (via the existing price-update path used by `updateUOM`) and records `selectedPriceList`, then triggers the existing pricing/tax recalculation.

### 4. Cart line UI (`CartItemRow`)
When `posDetails.custom_quick_switch_price` is enabled:
- On mount / item change, fetch `get_item_prices_across_price_lists(item_code, uom, customer)`.
- Render a single horizontal row of compact pills beneath the line, one per returned price list:
  - Label: `<name truncated to 8 chars> <rate>`.
  - The pill whose `price_list` matches the line's active price list (or, before any click, the line's current effective list) is highlighted.
  - Compact styling so ~5 pills fit one row; overflow scrolls horizontally (no wrap).
- Click a pill → `setItemRate(item.id, rate, price_list)`.

### 5. Coexistence
Independent of `allow_price_list_switching` (whole-cart switch). A pill click is a per-line override; existing discount/tax logic recalculates on the new base rate.

## Testing
- **Backend:** unit test `get_item_prices_across_price_lists` returns one entry per price list that has a rate, omits missing ones, respects UOM. (Mock Item Price lookups or seed test data.)
- **Frontend:** `npm run build` gate (tsc unusable in this repo); manual — enable the setting, add an item, see pills, click one, confirm the line rate and totals update and the pill highlights.

## Files Touched (anticipated)
- `klik_pos/klik_pos/custom/pos_profile.json` — `custom_quick_switch_price` checkbox.
- `klik_pos/api/item/item_price.py` — `get_item_prices_across_price_lists`.
- `klik_spa/src/stores/cartStore.ts` — per-line `selectedPriceList` + `setItemRate`.
- `klik_spa/src/components/order/CartItemRow.tsx` — pill row + fetch + click.
- `klik_pos/tests/` — endpoint test.

## Out of scope (YAGNI)
- Pills on product tiles / product browser (cart-line only for now).
- Persisting per-line price-list choice across hold/resume (can be added later if needed).
- Bulk prefetch of all visible products' rates.

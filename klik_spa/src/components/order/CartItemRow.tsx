"use client";

import { Fragment, useState, useEffect, useCallback, useRef, type ReactNode } from "react";
import { Minus, Plus, X, Package, ChevronDown, ChevronUp, AlertTriangle, Eye, Pencil } from "lucide-react";
import { toast } from "react-toastify";
import type { BundleEntry, CartItem, ItemTaxInfo } from "../../../types";
import { formatCurrencyWithSymbol } from "../../utils/currency";
import { UOMSelectField } from "./UOMSelectField";
import { nextUom, switchUom, uomMaps } from "../../utils/uomSwitch";
import { SerialBatchBundleModal } from "./SerialBatchBundleSelector";
import { useCartStore } from "../../stores/cartStore";
import { usePOSProfileStore } from "../../stores/posProfileStore";
import ProductDetailsModal from "../ProductDetailsModal";
import DescriptionDialog from "./DescriptionDialog";
import { CART_ROW_GRID } from "./cartTableLayout";
import { getEffectiveDisplayRate, getEffectiveItemRate, getExclusiveTaxRateForItem, getCartRowPctBasis, getPreDiscountRate } from "../../utils/cartPricing";
import { getCartNetWeight } from "../../utils/cartWeight";
import { formatNumber } from "../../utils/numberInput";
import { roundCurrency } from "../../utils/currencyMath";
import { NumberField } from "../ui/NumberField";
import { RatePriceField } from "./RatePriceField";

// Line panel: compact edit row on top, then label-left / value-right ledger rows.
const PANEL_LABEL = "block text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1 truncate";
const PANEL_INPUT = "w-full h-9 px-2 text-sm text-right border border-gray-300 dark:border-gray-600 rounded-md focus:ring-2 focus:ring-beveren-500 focus:border-transparent bg-white dark:bg-gray-800 text-gray-900 dark:text-white read-only:bg-gray-100 dark:read-only:bg-gray-700";
const LEDGER_ROW = "flex items-center justify-between gap-3 min-h-[1.5rem]";
const LEDGER_LABEL = "text-gray-500 dark:text-gray-400 shrink-0";
import { getItemDisplayName } from "../../utils/itemDisplayName";
import { getCostMargin, getInclusiveTaxRate } from "../../utils/costMargin";
import { costMode, pickCost } from "../../utils/costDisplay";
import PriceListPopup from "../PriceListPopup";
import {
  buildPriceOptions,
  computePricePopupPosition,
  cyclePriceOptionIndex,
  linePriceShown,
  resolveActivePriceList,
  type PricePopupPosition,
} from "../../utils/priceOptions";
import { rateBoxMinimum } from "../../utils/priceFloor";
import { discountAmountFromPct, discountPctFromAmount } from "../../utils/lineEdits";
import { uomLines, visibleFacts, type LineInfo } from "../../utils/lineInfo";

interface CartItemRowProps {
  item: CartItem;
  itemId: string;
  isExpanded: boolean;
  onToggleExpand: () => void;
  itemDiscount: any;
  onUpdateQuantity: (id: string, quantity: number) => void;
  onRemoveItem?: (id: string) => void;
  onUOMChange: (itemId: string, uom: string, price: number, conversionFactor?: number) => void;
  onDescriptionChange?: (itemId: string, description: string) => void;
  onDiscountChange: (itemId: string, field: string, value: number | string) => void;
  onCustomRateChange: (item: CartItem, rate?: number, includesTax?: boolean) => void;
  onDuplicateItem: (item: CartItem) => void;
  onBundleUpdate?: (itemId: string, bundleId: string, entries: any[]) => void;
  selectedCustomer?: { id: string } | null;
  posDetails: any;
  itemBatches: any[];
  itemSerials: string[];
  currency_symbol?: string;
  isMobile?: boolean;
  autoFetchBatch?: boolean;
  /** "facts": the grid till's side panel. The grid row edits the line, so the panel shows the
   * item's facts as the till's POS Profile lists them, under the name, chip and Serial/Batch. */
  variant?: "row" | "facts";
}

interface BatchData {
  batch_no: string;
  qty: number;
  expiry_date: string;
  manufacturing_date: string;
}

interface SerialData {
  serial_no: string;
}

interface PriceListEntry {
  price_list: string;
  rate: number;
  currency: string;
  uom?: string;
  customer?: string;
  note?: string;
  cost: number;
  margin: number;
  margin_pct: number;
}

interface WarehouseStock {
  warehouse: string;
  bal_qty: number;
  val_rate: number;
}

interface ItemFullData {
  item_name: string;
  item_code: string;
  standard_rate: number;
  valuation_rate: number;
  /** Per stock UOM; absent when the till's Cost Display is Hidden. */
  last_purchase_rate?: number;
  last_purchase_date?: string | null;
  price_lists: PriceListEntry[];
  batches: BatchData[];
  serials: SerialData[];
  warehouse_stock: WarehouseStock[];
  uom: string;
  brand?: string;
  has_batch_no: number;
  has_serial_no: number;
  total_bal_qty: number;
  global_stock_total?: {
    total_qty: number;
    total_value: number;
    avg_valuation_rate: number;
  };
}

export const CartItemRow = ({
  item,
  itemId,
  isExpanded,
  onToggleExpand,
  itemDiscount,
  onUpdateQuantity,
  onRemoveItem,
  onUOMChange,
  onDiscountChange,
  onCustomRateChange,
  onDuplicateItem,
  onBundleUpdate,
  onDescriptionChange,
  selectedCustomer,
  posDetails,
  currency_symbol,
  isMobile,
  autoFetchBatch = false,
  variant = "row",
}: CartItemRowProps) => {
  const facts = variant === "facts";
  const { updateItemBundleEntries, adjustQuantity } = useCartStore();
  const cartPriceList = useCartStore((s) => s.selectedPriceList);
  const quoteMode = useCartStore((s) => s.quoteMode);
  const highlightItemId = useCartStore((s) => s.highlightItemId);
  const highlightNonce = useCartStore((s) => s.highlightNonce);
  const [glowing, setGlowing] = useState(false);

  useEffect(() => {
    if (highlightItemId !== item.id) {
      // Highlight moved to another item (or cleared) — make sure this row
      // never stays stuck glowing, even on rapid successive clicks.
      setGlowing(false);
      return;
    }
    setGlowing(true);
    const t = setTimeout(() => setGlowing(false), 1200);
    return () => clearTimeout(t);
  }, [highlightItemId, highlightNonce, item.id]);
  const [showBundleModal, setShowBundleModal] = useState(false);
  const [isBundleDetailsOpen, setIsBundleDetailsOpen] = useState(false);
  const [showProductModal, setShowProductModal] = useState(false);
  const [showDescriptionDialog, setShowDescriptionDialog] = useState(false);
  const [bundleEntries, setBundleEntries] = useState<BundleEntry[]>(() => {
    if (item.bundle_entries && Array.isArray(item.bundle_entries)) {
      return item.bundle_entries;
    }
    if (itemDiscount.bundle_entries && typeof itemDiscount.bundle_entries === "string") {
      try {
        return JSON.parse(itemDiscount.bundle_entries);
      } catch {
        return [];
      }
    }
    return [];
  });
  const [availableBatches, setAvailableBatches] = useState<BatchData[]>([]);
  const [availableSerials, setAvailableSerials] = useState<SerialData[]>([]);
  const [isFetchingBundleData, setIsFetchingBundleData] = useState(false);
  const [modalEntries, setModalEntries] = useState<BundleEntry[]>([]);
  const [modalQty, setModalQty] = useState(item.quantity);
  const [linePricePopup, setLinePricePopup] = useState<{ selectedIndex: number; position: PricePopupPosition } | null>(null);
  const priceTriggerRef = useRef<HTMLButtonElement>(null);

  // The % is of the custom rate on a custom-rate line, else of the list price.
  const isTaxIncludedInBasicRate = posDetails?.is_tax_included_in_basic_rate === 1
    || posDetails?.is_tax_included_in_basic_rate === "1"
    || posDetails?.is_tax_included_in_basic_rate === true;
  const preDiscountPrice = getCartRowPctBasis(item, itemDiscount, isTaxIncludedInBasicRate);
  const [localDiscountPct, setLocalDiscountPct] = useState<number>(() =>
    discountPctFromAmount(preDiscountPrice, itemDiscount.discountAmount || 0));

  useEffect(() => {
    setLocalDiscountPct(discountPctFromAmount(preDiscountPrice, itemDiscount.discountAmount || 0));
  }, [itemDiscount.discountAmount, preDiscountPrice]);
  const [fullItemData, setFullItemData] = useState<ItemFullData | null>(null);
  const [isLoadingFullData, setIsLoadingFullData] = useState(false);
  const [lineInfo, setLineInfo] = useState<LineInfo | null>(null);
  useEffect(() => {
    if (!facts) return;
    let live = true;
    setLineInfo(null);
    fetch(`/api/method/klik_pos.api.item.line_info.get_line_info?item_code=${encodeURIComponent(item.item_code || item.id)}`)
      .then((r) => r.json())
      .then((res) => { if (live) setLineInfo(res?.message ?? null); })
      .catch((e) => console.error("Line info failed:", e));
    return () => { live = false; };
  }, [facts, item.item_code, item.id]);

  const hasSerialOrBatch = item.has_serial_no || item.has_batch_no;
  const pickedWarehouse = usePOSProfileStore((s) => s.warehouse);
  const warehouse = pickedWarehouse || posDetails?.warehouse || "";
  const costDisplayMode = costMode(posDetails);
  const restrictCostVisibility = costDisplayMode === "Hidden";
  const allowPriceListSwitching = !!posDetails?.allow_price_list_switching;
  const quickSwitchPrice = !!posDetails?.custom_quick_switch_price;

  const fetchFullItemDetails = useCallback(async () => {
    if (!warehouse) return;
    setIsLoadingFullData(true);
    try {
      const response = await fetch(
        `/api/method/klik_pos.api.item.item_details.get_full_pricing_and_batch_details?item_code=${encodeURIComponent(item.item_code || item.id)}&warehouse=${encodeURIComponent(warehouse)}`
      );
      const res = await response.json();
      if (res?.message) {
        setFullItemData(res.message);
      }
    } catch (error) {
      console.error("Failed to fetch full item details:", error);
    } finally {
      setIsLoadingFullData(false);
    }
  }, [item.item_code, item.id, warehouse]);

  useEffect(() => {
    if ((isExpanded || quickSwitchPrice) && warehouse) {
      fetchFullItemDetails();
    }
  }, [isExpanded, quickSwitchPrice, warehouse, fetchFullItemDetails]);

  // The popup is position: fixed, so it would stay put while the cart scrolls out from under
  // it, and it sits above everything else - close it on any outside press, scroll or resize.
  const isLinePricePopupOpen = !!linePricePopup;
  useEffect(() => {
    if (!isLinePricePopupOpen) return;
    const close = () => setLinePricePopup(null);
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (priceTriggerRef.current?.contains(target)) return;
      if (document.querySelector("[data-price-popup]")?.contains(target)) return;
      close();
    };
    document.addEventListener("mousedown", handlePointerDown, true);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown, true);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [isLinePricePopupOpen]);

  const saveToCart = useCallback((entries: BundleEntry[]) => {
    const validEntries = entries.map(({ selected, ...e }) => e);
    setBundleEntries(validEntries);
    updateItemBundleEntries(item.id, validEntries);
    if (onBundleUpdate) {
      onBundleUpdate(item.id, "", validEntries);
    }
  }, [item.id, updateItemBundleEntries, onBundleUpdate]);

  const fetchBundleData = useCallback(async (qty: number, shouldSaveToCart: boolean = false) => {
    if (!warehouse) return;
    if (!item.has_serial_no && !item.has_batch_no) return;

    setIsFetchingBundleData(true);
    try {
      const params = new URLSearchParams({
        item_code: item.item_code || item.id,
        warehouse: warehouse,
        customer: selectedCustomer?.id || "",
        qty: qty.toString(),
        based_on: "FIFO",
        has_serial_no: item.has_serial_no ? "1" : "0",
        has_batch_no: item.has_batch_no ? "1" : "0",
      });

      const response = await fetch(`/api/method/klik_pos.api.item.bundle.get_available_batches_and_serials?${params.toString()}`);
      const result = await response.json();

      if (result.message) {
        const data = result.message;
        if (data.batches && Array.isArray(data.batches)) {
          setAvailableBatches(data.batches);
        } else {
          setAvailableBatches([]);
        }
        if (data.serials && Array.isArray(data.serials)) {
          setAvailableSerials(data.serials);
        } else {
          setAvailableSerials([]);
        }
      }

      if (qty > 0) {
        const autoDataResponse = await fetch(`/api/method/erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle.get_auto_data?${params.toString()}`);
        const autoDataResult = await autoDataResponse.json();

        if (autoDataResult.message && Array.isArray(autoDataResult.message) && autoDataResult.message.length > 0) {
          const autoEntries = autoDataResult.message.map((row: any) => ({
            serial_no: row.serial_no || undefined,
            batch_no: row.batch_no || undefined,
            warehouse: row.warehouse || warehouse,
            qty: row.qty || 1,
            selected: false,
          }));
          setModalEntries(autoEntries);
          if (shouldSaveToCart) {
            saveToCart(autoEntries);
          }
        } else {
          setModalEntries([{
            qty: 1,
            selected: false,
            serial_no: item.has_serial_no ? "" : undefined,
            batch_no: item.has_batch_no ? "" : undefined,
            warehouse,
          }]);
          if (shouldSaveToCart) {
            saveToCart([]);
          }
        }
      }
    } catch (error) {
      console.error("Failed to fetch bundle data:", error);
      toast.error("Failed to fetch batch/serial data");
    } finally {
      setIsFetchingBundleData(false);
    }
  }, [item, warehouse, selectedCustomer, saveToCart]);

  useEffect(() => {
    if (hasSerialOrBatch && warehouse && autoFetchBatch && item.quantity > 0) {
      fetchBundleData(item.quantity, true);
    }
  }, [item.quantity, warehouse, autoFetchBatch, hasSerialOrBatch]);

  useEffect(() => {
    if (bundleEntries.length > 0 && !autoFetchBatch) {
      setModalEntries(bundleEntries);
    }
  }, [bundleEntries, autoFetchBatch]);

  const handleOpenModal = () => {
    if (hasSerialOrBatch) {
      setModalQty(item.quantity);
      if (!autoFetchBatch && item.quantity > 0) {
        fetchBundleData(item.quantity, false);
      } else if (autoFetchBatch && bundleEntries.length === 0 && item.quantity > 0) {
        fetchBundleData(item.quantity, true);
      }
      setShowBundleModal(true);
    }
  };

  const handleBundleSave = (entries: BundleEntry[]) => {
    if (!Array.isArray(entries)) {
      toast.error("Invalid bundle entries");
      return;
    }
    
    const totalBundleQty = entries.reduce((sum, entry) => sum + (Number(entry.qty) || 0), 0);
    
    if (totalBundleQty !== item.quantity) {
      onUpdateQuantity(item.id, totalBundleQty);
    }
    
    saveToCart(entries);
    setShowBundleModal(false);
  };

  const handleModalQtyChange = async (newQty: number) => {
    setModalQty(newQty);
    if (autoFetchBatch && newQty > 0) {
      await fetchBundleData(newQty, true);
    }
  };

  const handleModalFetchData = async (qty: number) => {
    await fetchBundleData(qty, true);
  };

  const handleRateChange = (value?: number) => {
    if (value === undefined || value === null || Number.isNaN(value)) {
      // Back to the cart list's price, so the line no longer claims the list it was switched to.
      if (itemDiscount.selectedPriceList) onDiscountChange(item.id, "selectedPriceList", "");
      onCustomRateChange(item, undefined);
      onDiscountChange(item.id, "discountAmount", 0);
      setLocalDiscountPct(0);
      return;
    }

    const rate = Math.max(0, value);
    onCustomRateChange(item, rate, isTaxIncludedInBasicRate);
    onDiscountChange(item.id, "discountAmount", 0);
    setLocalDiscountPct(0);
  };

  const handleDiscountPercentageChange = (value: number) => {
    const pct = Math.min(100, Math.max(0, value || 0));
    const amt = discountAmountFromPct(preDiscountPrice, pct);
    setLocalDiscountPct(pct);
    onDiscountChange(item.id, "discountAmount", amt);
  };

  const handleDiscountAmountChange = (value: number) => {
    const amt = Math.max(0, value || 0);
    onDiscountChange(item.id, "discountAmount", amt);
    setLocalDiscountPct(discountPctFromAmount(preDiscountPrice, amt));
  };

  // `entry` pins the exact row picked: one price list can have several Item Prices (per UOM,
  // per customer), and looking it up again by name alone would apply whichever comes first.
  const handleLinePriceListChange = (priceListName: string, entry?: PriceListEntry) => {
    onDiscountChange(item.id, "selectedPriceList", priceListName);
    if (!priceListName) {
      handleRateChange(undefined);
      return;
    }

    const matchingPrice = entry || fullItemData?.price_lists?.find((priceList) =>
      priceList.price_list === priceListName
      && (!priceList.uom || priceList.uom === item.uom)
    ) || fullItemData?.price_lists?.find((priceList) => priceList.price_list === priceListName);

    if (matchingPrice) {
      onCustomRateChange(item, Number(matchingPrice.rate || 0), false);
      onDiscountChange(item.id, "discountAmount", 0);
      setLocalDiscountPct(0);
    }
  };

  const linePriceLists = (quickSwitchPrice ? fullItemData?.price_lists ?? [] : []).filter(
    (priceList) => (!priceList.uom || priceList.uom === item.uom) && Number(priceList.rate || 0) > 0
  );
  const linePriceOptions = buildPriceOptions(linePriceLists, false, 0);
  const cartPriceListName = resolveActivePriceList(
    cartPriceList,
    (selectedCustomer as { sellingPriceList?: string } | null | undefined)?.sellingPriceList,
    posDetails?.selling_price_list
  );
  const {
    entry: shownLinePriceList,
    active: activeLinePriceList,
    customRate: shownCustomRate,
  } = linePriceShown(linePriceLists, itemDiscount.selectedPriceList, itemDiscount.customRate, cartPriceListName);
  const linePriceListLabel = shownLinePriceList
    ? `${shownLinePriceList.price_list} ${Number(shownLinePriceList.rate || 0).toFixed(2)}`
    : shownCustomRate !== null
      ? `Custom ${shownCustomRate.toFixed(2)}`
      : cartPriceListName || "Price list";

  const openLinePricePopup = () => {
    const rect = priceTriggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const activeIndex = shownLinePriceList ? linePriceLists.indexOf(shownLinePriceList) : 0;
    setLinePricePopup({
      selectedIndex: Math.max(0, activeIndex),
      position: computePricePopupPosition(rect, linePriceOptions.length, {
        width: window.innerWidth,
        height: window.innerHeight,
      }),
    });
  };

  const commitLinePriceOption = (index: number) => {
    const option = linePriceOptions[index];
    setLinePricePopup(null);
    if (option) handleLinePriceListChange(option.label, linePriceLists[index]);
  };

  const handlePriceTriggerKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const isActivate = e.key === "Enter" || e.key === " ";
    if (!linePricePopup) {
      // Closed: only opening is ours. Everything else (F3, Backspace-to-search, Escape to
      // collapse the line) must still reach the row and the POS-wide shortcuts.
      if (isActivate) {
        e.preventDefault();
        e.stopPropagation();
        openLinePricePopup();
      }
      return;
    }
    // Open: keep navigation keys away from the row and the POS-wide shortcuts.
    e.stopPropagation();
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const direction = e.key === "ArrowDown" ? 1 : -1;
      setLinePricePopup((p) =>
        p ? { ...p, selectedIndex: cyclePriceOptionIndex(p.selectedIndex, linePriceOptions.length, direction) } : p
      );
    } else if (isActivate) {
      e.preventDefault();
      commitLinePriceOption(linePricePopup.selectedIndex);
    } else if (e.key === "Escape" || e.key === "Tab") {
      setLinePricePopup(null);
    }
  };

  const discountedPrice = getEffectiveItemRate(item, {
    itemDiscounts: { [item.id]: itemDiscount },
    isTaxIncludedInBasicRate,
  });
  const displayRateInclTax = getEffectiveDisplayRate(item, {
    itemDiscounts: { [item.id]: itemDiscount },
    isTaxIncludedInBasicRate,
  });
  const exclusiveTaxRate = getExclusiveTaxRateForItem(item, { isTaxIncludedInBasicRate });
  const hasExclusiveTax = exclusiveTaxRate > 0;
  const totalTaxRate = hasExclusiveTax ? exclusiveTaxRate : Number(item.total_tax_rate || 0);
  const taxAmountPerUnit = roundCurrency(Math.max(0, displayRateInclTax - discountedPrice));
  // original_price is the rate before any rule discount; price is what it costs now.
  // Reading item.price here showed no strike-through at all once rule discounts stopped
  // being replayed through itemDiscounts.
  const listRate = Number((item as CartItem & { original_price?: number }).original_price || 0) || item.price;
  const originalTotal = roundCurrency(Math.max(listRate, discountedPrice) * item.quantity);
  // ERPNext convention: line displays the price-list (net) rate; tax shows in totals.
  const discountedTotal = roundCurrency(discountedPrice * item.quantity);
  const amount = discountedTotal;

  const hasBundleEntries = bundleEntries.length > 0;

  const cycleUom = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      const customerParam = selectedCustomer?.id ? `&customer=${encodeURIComponent(selectedCustomer.id)}` : "";
      const response = await fetch(
        `/api/method/klik_pos.api.item.item_details.get_item_uoms_and_prices?item_code=${encodeURIComponent(item.item_code || item.id)}${customerParam}`,
        { credentials: "include" }
      );
      const maps = uomMaps((await response.json())?.message || {});
      const to = nextUom(maps.uoms, item.uom);
      if (!to) {
        toast.info(`${item.name} is sold in one unit only`);
        return;
      }
      const next = switchUom(maps, item.uom || "", item.price, to);
      onUOMChange(item.id, next.uom, next.price, next.conversionFactor);
    } catch (error) {
      console.error("Could not switch unit:", error);
      toast.error("Could not switch unit");
    }
  };

  const currentWarehouseStock = fullItemData?.warehouse_stock?.find(wh => wh.warehouse === warehouse);
  // Stock is counted in the stock UOM; the line may be in another, e.g. kg of a wire stocked in rolls.
  const uomFactor = Number(item.conversion_factor) > 0 ? Number(item.conversion_factor) : 1;
  const availableStock = (currentWarehouseStock?.bal_qty || 0) / uomFactor;
  const availableStockLabel = availableStock.toLocaleString(undefined, { maximumFractionDigits: 2 });
  const valuationRate = typeof currentWarehouseStock?.val_rate === "number" ? currentWarehouseStock.val_rate : null;
  // val_rate is per stock UOM and never includes sales tax; discountedPrice is per the line's
  // UOM and includes whatever tax is baked into the rate. The list's tax_info (carried onto the
  // cart item) knows the POS Profile's tax template too; a line restored without it falls back
  // to the cart's own tax fields, which only see an Item Tax Template.
  const listTaxInfo = (item as CartItem & { tax_info?: ItemTaxInfo }).tax_info;
  const inclusiveTaxRate = listTaxInfo
    ? getInclusiveTaxRate(listTaxInfo)
    : !hasExclusiveTax ? totalTaxRate : 0;
  const isInclusiveTax = inclusiveTaxRate > 0;
  // The till's Cost Display picks the cost the margin is worked from; the other is shown beside it.
  const { shown: costRate, other: otherCostRate, otherLabel } = pickCost(
    costDisplayMode, valuationRate, fullItemData?.last_purchase_rate,
  );
  const costLabel = otherLabel === "Valuation" ? "Last purchase" : "Valuation";
  const hasValidValuationRate = costRate > 0;
  const inclTax = (perStockUom: number) => getCostMargin({
    sellPrice: discountedPrice,
    costPerStockUom: perStockUom,
    inclusiveTaxRate,
    conversionFactor: item.conversion_factor,
  });
  const { costInclTax: valuationRateInclTax, margin: lineMargin } = inclTax(costRate);
  const otherCostInclTax = inclTax(otherCostRate).costInclTax;
  const lastPurchaseDate = fullItemData?.last_purchase_date;
  const isNegativeMargin = hasValidValuationRate ? lineMargin < 0 : false;
  const marginAmount = hasValidValuationRate ? lineMargin : 0;
  const marginPercentage = hasValidValuationRate ? (marginAmount / valuationRateInclTax) * 100 : 0;

  const showPositiveMarginWarning = !restrictCostVisibility && hasValidValuationRate && lineMargin > 0;
  const showNegativeMarginWarning = !restrictCostVisibility && hasValidValuationRate && isNegativeMargin && discountedPrice > 0;
  const showStockWarning = item.quantity > availableStock && availableStock > 0;
  const showNoStockWarning = availableStock === 0;

  // The panel's Rate is the rate before its discount, as in the grid; changing it clears the discount.
  const panelRate = hasExclusiveTax
    ? getPreDiscountRate(item, { itemDiscounts: { [item.id]: itemDiscount }, isTaxIncludedInBasicRate })
    : getEffectiveDisplayRate(item, { itemDiscounts: { [item.id]: { ...itemDiscount, discountAmount: 0 } }, isTaxIncludedInBasicRate });
  const commitPanelRate = (n: number | undefined, text: string) => {
    if (n === undefined && text !== "") return;
    if (n === undefined) return handleRateChange(undefined);
    // Discount off on a list-floor till: the rate cannot go under the line's list.
    const minimum = rateBoxMinimum(
      posDetails,
      itemDiscount.selectedPriceList && shownLinePriceList ? Number(shownLinePriceList.rate || 0) : listRate,
    );
    const rate = Math.max(0, n);
    if (minimum !== null && rate < minimum) {
      handleRateChange(minimum);
      toast.warning(`${item.name}: can't sell below ${formatCurrencyWithSymbol(minimum, currency_symbol)}. Rate reset to the minimum.`);
      return;
    }
    handleRateChange(rate);
  };
  const lineWeight = getCartNetWeight([item]);
  // Margin bar: both on one scale, the larger of rate and cost plus headroom.
  const barScale = Math.max(discountedPrice, valuationRateInclTax, otherCostInclTax) * 1.15 || 1;
  const marginBar = {
    rate: Math.min(100, (Math.max(0, discountedPrice) / barScale) * 100),
    cost: Math.min(100, (valuationRateInclTax / barScale) * 100),
    other: Math.min(100, (otherCostInclTax / barScale) * 100),
  };

  // The line's quantity box: more than is in stock is capped unless the till allows it.
  const commitLineQty = (typed: number) => {
    const available = item.available ?? 0;
    if (!quoteMode && !(posDetails?.custom_enable_loss_of_sale && !item.has_serial_no && !item.has_batch_no) && available > 0 && typed > available && !item.allow_negative_stock) {
      onUpdateQuantity(item.id, available);
      toast.warning(`Only ${available} units available. Quantity set to ${available}.`);
    } else {
      onUpdateQuantity(item.id, typed);
    }
  };

  // Each fact once: the cart row draws them in a fixed order, the facts panel in the till's.
  const priceChip = linePriceOptions.length > 0 ? (
                <button
                  ref={priceTriggerRef}
                  type="button"
                  aria-haspopup="listbox"
                  aria-expanded={!!linePricePopup}
                  onClick={(e) => { e.stopPropagation(); if (linePricePopup) setLinePricePopup(null); else openLinePricePopup(); }}
                  onKeyDown={handlePriceTriggerKeyDown}
                  // Firefox clicks a button on Space keyup even when keydown was prevented,
                  // which would reopen the popup straight after a Space commit.
                  onKeyUp={(e) => { if (e.key === " ") e.preventDefault(); }}
                  onBlur={() => setLinePricePopup(null)}
                  title={
                    shownLinePriceList
                      ? `${shownLinePriceList.price_list}: ${formatCurrencyWithSymbol(Number(shownLinePriceList.rate || 0), currency_symbol)} - switch price list`
                      : "Switch price list for this line"
                  }
                  className={`max-w-full inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors ${
                    activeLinePriceList
                      ? "border-beveren-500 bg-beveren-50 text-beveren-700 dark:bg-beveren-900/30 dark:text-beveren-300"
                      : "border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:border-beveren-300"
                  }`}
                >
                  <span className="truncate font-mono">
                    {linePriceListLabel}
                  </span>
                  <ChevronDown size={10} className="flex-shrink-0" />
                </button>
              ) : null;
  const serialBatch = (
    <>
      {hasSerialOrBatch ? (
                <button
                  type="button"
                  onClick={handleOpenModal}
                  className={`w-full flex items-center justify-center gap-2 h-9 rounded-md border text-sm font-medium transition-colors ${
                    hasBundleEntries
                      ? "border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 hover:bg-green-100 dark:hover:bg-green-900/30"
                      : "border-blue-300 dark:border-blue-700 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/30"
                  }`}
                >
                  <Package size={14} />
                  {hasBundleEntries ? "Update Serial/Batch" : "Add Serial/Batch"}
                </button>
              ) : null}
      {hasBundleEntries ? (
                <div className="rounded-md border border-blue-200 dark:border-blue-800 overflow-hidden">
                  <button
                    type="button"
                    onClick={() => setIsBundleDetailsOpen(!isBundleDetailsOpen)}
                    className="w-full flex items-center justify-between p-2 bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors"
                  >
                    <span className="text-xs text-blue-800 dark:text-blue-300 font-medium">Batch/Serial Details:</span>
                    {isBundleDetailsOpen ? (
                      <ChevronUp size={14} className="text-blue-800 dark:text-blue-300" />
                    ) : (
                      <ChevronDown size={14} className="text-blue-800 dark:text-blue-300" />
                    )}
                  </button>
                  {isBundleDetailsOpen ? (
                    <div className="p-2 pt-0 bg-blue-50 dark:bg-blue-900/20 space-y-1">
                      {bundleEntries.map((entry, idx) => (
                        <div key={idx} className="text-xs text-blue-700 dark:text-blue-400">
                          {entry.serial_no ? <span>Serial: {entry.serial_no} </span> : null}
                          {entry.batch_no ? <span>Batch: {entry.batch_no} </span> : null}
                          {entry.qty ? <span>Qty: {entry.qty}</span> : null}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
    </>
  );
  const stockRow = currentWarehouseStock || showNoStockWarning ? (
                  <div className={LEDGER_ROW}>
                    <span className={LEDGER_LABEL}>In stock</span>
                    <span className={`text-right tabular-nums ${showNoStockWarning ? "text-red-600 dark:text-red-400" : showStockWarning ? "text-amber-600 dark:text-amber-400" : "text-gray-900 dark:text-white"}`}>
                      {(showNoStockWarning || showStockWarning) ? <AlertTriangle size={12} className="inline mr-1 -mt-0.5" /> : null}
                      {availableStockLabel} {item.uom || fullItemData?.uom}
                      <span className="text-xs text-gray-500 dark:text-gray-400"> · {warehouse}</span>
                    </span>
                  </div>
                ) : null;
  const weightRow = lineWeight.total > 0 ? (
                  <div className={LEDGER_ROW}>
                    <span className={LEDGER_LABEL}>Weight</span>
                    <span className="text-right tabular-nums text-gray-900 dark:text-white">
                      {formatNumber(lineWeight.total, "qty")} {lineWeight.uom}
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {" "}· {formatNumber(Number(item.weight_per_unit), "qty")} / {item.base_uom || item.uom}
                      </span>
                    </span>
                  </div>
                ) : null;
  const costRows: Record<string, ReactNode> = !restrictCostVisibility && currentWarehouseStock
    ? {
        [costLabel]: (
          <div className={LEDGER_ROW}>
                      <span className={LEDGER_LABEL}>{costLabel}</span>
                      <span className="text-right tabular-nums text-gray-900 dark:text-white">
                        {hasValidValuationRate ? formatNumber(valuationRateInclTax, "money") : "—"}
                        {hasValidValuationRate ? (
                          <span className="text-xs text-gray-500 dark:text-gray-400">
                            {isInclusiveTax
                              ? ` incl. VAT · ${formatNumber(costRate * uomFactor, "money")} excl.`
                              : ` / ${item.uom || fullItemData?.uom}`}
                          </span>
                        ) : null}
                      </span>
                    </div>
        ),
        [otherLabel]: (
          <div className={LEDGER_ROW}>
                      <span className={LEDGER_LABEL}>{otherLabel}</span>
                      <span className="text-right tabular-nums text-gray-500 dark:text-gray-400">
                        {otherCostRate > 0
                          ? `${formatNumber(otherCostInclTax, "money")}${otherLabel === "Last purchase" && lastPurchaseDate ? ` · ${lastPurchaseDate}` : ""}`
                          : otherLabel === "Last purchase" ? "no purchase invoice" : "—"}
                      </span>
                    </div>
        ),
      }
    : {};
  const marginRows = (showPositiveMarginWarning || showNegativeMarginWarning) ? (
                  <>
                    <div className={LEDGER_ROW}>
                      <span className={LEDGER_LABEL}>Margin</span>
                      <span className={`text-right tabular-nums font-semibold ${isNegativeMargin ? "text-red-600 dark:text-red-400" : "text-green-700 dark:text-green-400"}`}>
                        {isNegativeMargin ? "" : "+"}{formatNumber(marginAmount, "money")}
                        <span className={`ml-1.5 rounded-full px-1.5 py-0.5 text-[11px] ${isNegativeMargin ? "bg-red-100 dark:bg-red-900/40" : "bg-green-100 dark:bg-green-900/40"}`}>
                          {marginPercentage > 0 ? "+" : ""}{marginPercentage.toFixed(1)}%
                        </span>
                      </span>
                    </div>
                    {/* Rate against cost: the bar is the rate, the solid tick the cost the margin
                        is worked from, the dashed tick the other cost. */}
                    <div
                      className="relative h-1.5 rounded-full bg-gray-200 dark:bg-gray-700"
                      title={`Solid: ${costLabel} ${formatNumber(valuationRateInclTax, "money")}${otherCostRate > 0 ? ` · Dashed: ${otherLabel} ${formatNumber(otherCostInclTax, "money")}` : ""}`}
                    >
                      <div
                        className={`absolute inset-y-0 left-0 rounded-full ${isNegativeMargin ? "bg-red-500" : "bg-green-500"}`}
                        style={{ width: `${marginBar.rate}%` }}
                      />
                      <div title={costLabel} className="absolute -top-1 -bottom-1 w-0.5 bg-gray-900 dark:bg-white" style={{ left: `${marginBar.cost}%` }} />
                      {otherCostRate > 0 ? (
                        <div title={otherLabel} className="absolute -top-1 -bottom-1 border-l-2 border-dashed border-gray-500 dark:border-gray-300" style={{ left: `${marginBar.other}%` }} />
                      ) : null}
                    </div>
                  </>
                ) : null;
  const builtinFact: Record<string, ReactNode> = {
    in_stock: stockRow,
    weight: weightRow,
    valuation: costRows["Valuation"],
    last_purchase: costRows["Last purchase"],
    margin: marginRows,
    warehouses: lineInfo?.warehouses.length ? (
      <div className="flex flex-col gap-0.5">
        <span className={LEDGER_LABEL}>Warehouses</span>
        {lineInfo.warehouses.map((w) => (
          <div key={w.warehouse} className={`flex justify-between gap-3 text-xs tabular-nums ${w.warehouse === warehouse ? "font-semibold text-gray-900 dark:text-white" : "text-gray-600 dark:text-gray-300"}`}>
            <span className="truncate" title={w.warehouse}>{w.warehouse}</span>
            <span className="shrink-0">{formatNumber(w.qty, "qty")} {lineInfo.stock_uom}</span>
          </div>
        ))}
      </div>
    ) : null,
    uoms: lineInfo?.uoms.length ? (
      <div className={LEDGER_ROW}>
        <span className={LEDGER_LABEL}>Units</span>
        <span className="text-right text-xs tabular-nums text-gray-900 dark:text-white">
          {uomLines(lineInfo).map((l) => <div key={l}>{l}</div>)}
        </span>
      </div>
    ) : null,
  };

  const bundleModal = (
    <SerialBatchBundleModal
          isOpen={showBundleModal}
          onClose={() => setShowBundleModal(false)}
          onSave={handleBundleSave}
          item={{
            id: item.id,
            item_code: item.item_code,
            name: item.name,
            has_serial_no: item.has_serial_no,
            has_batch_no: item.has_batch_no,
          }}
          warehouse={warehouse}
          qty={modalQty}
          onQtyChange={handleModalQtyChange}
          availableBatches={availableBatches}
          availableSerials={availableSerials}
          entries={modalEntries}
          onEntriesChange={setModalEntries}
          isLoading={isFetchingBundleData}
          onFetchData={handleModalFetchData}
          autoFetchBatch={autoFetchBatch}
        />
  );
  const pricePopup = linePricePopup && linePriceOptions.length > 0 && (
    <PriceListPopup
          options={linePriceOptions}
          selectedIndex={linePricePopup.selectedIndex}
          customValue=""
          position={linePricePopup.position}
          currencySymbol={currency_symbol}
          onSelect={commitLinePriceOption}
        />
  );
  const productModal = showProductModal && (
    <ProductDetailsModal
          item={{
            id: item.item_code || item.id,
            name: item.name,
            item_code: item.item_code,
            category: item.category,
            image: item.image,
            currency_symbol: currency_symbol,
          }}
          warehouse={warehouse}
          onClose={() => setShowProductModal(false)}
        />
  );

  const descriptionDialog = (
    <DescriptionDialog
      isOpen={showDescriptionDialog}
      itemName={item.name}
      initialValue={item.description || ""}
      onSave={(value) => {
        onDescriptionChange?.(item.id, value);
        setShowDescriptionDialog(false);
      }}
      onClose={() => setShowDescriptionDialog(false)}
    />
  );

  if (facts) {
    const shown = visibleFacts(lineInfo, restrictCostVisibility);
    return (
      <>
        <div data-cart-item-id={itemId} className="px-3 py-3 flex flex-col gap-3 text-sm">
          <div className="flex items-start gap-2">
            <p className="min-w-0 flex-1 font-medium leading-tight text-gray-900 dark:text-white line-clamp-2 break-words" title={item.name}>
              {getItemDisplayName(item, !!posDetails?.custom_use_item_code_as_display_name)}
            </p>
            <button
              type="button"
              onClick={() => setShowDescriptionDialog(true)}
              className="shrink-0 w-7 h-7 rounded flex items-center justify-center text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
              title="Edit description"
              aria-label="Edit description"
            >
              <Pencil size={15} />
            </button>
            <button
              type="button"
              onClick={() => setShowProductModal(true)}
              className="shrink-0 w-7 h-7 rounded flex items-center justify-center text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
              title="View full details"
              aria-label="View full details"
            >
              <Eye size={16} />
            </button>
          </div>
          {linePriceOptions.length > 0 ? <div>{priceChip}</div> : null}
          {serialBatch}
          {!lineInfo || isLoadingFullData ? (
            <div className="flex items-center gap-2 py-1 text-xs text-gray-500">
              <div className="animate-spin rounded-full h-4 w-4 border-2 border-beveren-500 border-t-transparent" />
              Loading…
            </div>
          ) : (
            <div className="border-t border-gray-200 dark:border-gray-700 pt-2 flex flex-col gap-1.5">
              {shown.map((r) =>
                r.key in builtinFact ? (
                  <Fragment key={r.key}>{builtinFact[r.key]}</Fragment>
                ) : (r.value ?? "").length > 40 ? (
                  <div key={r.key} className="flex flex-col gap-0.5">
                    <span className={LEDGER_LABEL}>{r.label}</span>
                    <span className="text-gray-900 dark:text-white whitespace-pre-line break-words">{r.value}</span>
                  </div>
                ) : (
                  <div key={r.key} className={LEDGER_ROW}>
                    <span className={LEDGER_LABEL}>{r.label}</span>
                    <span className="min-w-0 text-right break-words text-gray-900 dark:text-white">{r.value}</span>
                  </div>
                ),
              )}
            </div>
          )}
        </div>
        {bundleModal}
        {pricePopup}
        {productModal}
        {descriptionDialog}
      </>
    );
  }

  return (
    <>
      <div
        data-cart-item-id={itemId}
        className={`transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/40 ${glowing ? "cart-item-glow" : ""} ${(item.los_qty ?? 0) > 0 ? "bg-amber-50/70 dark:bg-amber-900/10" : ""}`}
      >
        <div
          data-cart-item-id={itemId}
          tabIndex={0}
          className={`${isMobile ? "px-3 py-2" : "px-3 py-1.5"} cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-beveren-400/50`}
          onClick={onToggleExpand}
          onKeyDown={(e) => {
            if (e.key === "Escape" && isExpanded) {
              e.preventDefault();
              onToggleExpand();
              const el = document.getElementById("pos-search-input") as HTMLInputElement | null;
              el?.focus();
              el?.select();
            }
          }}
        >
          {/* Row A: chevron + name, given the full row width so it stops wrapping early.
              Line actions (edit/details/remove) sit at the right of this same row instead of
              stacked under the total, since they no longer need to share width with it. */}
          <div className="flex items-start gap-1">
            <svg
              className={`flex-shrink-0 w-3 h-3 mt-1 text-gray-400 dark:text-gray-500 transform transition-transform duration-200 ${
                isExpanded ? "rotate-90" : ""
              }`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-tight text-gray-900 dark:text-white line-clamp-2 break-words" title={item.name}>
                {getItemDisplayName(item, !!posDetails?.custom_use_item_code_as_display_name)}
              </p>
              {!!posDetails?.custom_show_item_code_in_product_list && (item.item_code || item.id) && (
                <p className="text-sm text-gray-900 dark:text-white leading-tight truncate">
                  {posDetails?.custom_use_item_code_as_display_name ? item.name : (item.item_code || item.id)}
                </p>
              )}
            </div>
            <div className="flex-shrink-0 flex items-center gap-0.5">
              {(item.los_qty ?? 0) > 0 && (
                <span
                  className="mr-0.5 rounded whitespace-nowrap bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                  title="Asked for but not in stock: recorded as Loss of Sale"
                >
                  LoS {item.los_qty}
                </span>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); setShowDescriptionDialog(true); }}
                className={`${isMobile ? "w-7 h-7" : "w-5 h-5"} rounded flex items-center justify-center text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-700 dark:hover:text-gray-200 transition-colors`}
                title="Edit description"
                aria-label="Edit description"
              >
                <Pencil size={isMobile ? 13 : 11} />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); setShowProductModal(true); }}
                className={`${isMobile ? "w-7 h-7" : "w-5 h-5"} rounded flex items-center justify-center text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-700 dark:hover:text-gray-200 transition-colors`}
                title="View full details"
                aria-label="View full details"
              >
                <Eye size={isMobile ? 14 : 12} />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onRemoveItem?.(item.id); }}
                className={`${isMobile ? "w-7 h-7" : "w-5 h-5"} rounded flex items-center justify-center text-gray-400 dark:text-gray-500 hover:bg-red-50 dark:hover:bg-red-900/20 hover:text-red-600 dark:hover:text-red-400 transition-colors`}
                title="Remove item"
                aria-label="Remove item"
              >
                <X size={isMobile ? 15 : 13} />
              </button>
            </div>
          </div>

          {/* Row B: price-list quick-switcher on the left, qty/rate/total on one line to the
              right - reuses CART_ROW_GRID so this row still lines up under the cart header's
              Qty/Rate/Total columns. */}
          <div className={`${CART_ROW_GRID} mt-1`}>
            {/* Quick Switch Price: one compact trigger; the choices drop down in the same popup
                the item list's '*' shortcut uses, instead of a pill per price list. */}
            <div className="min-w-0">
              {priceChip}
            </div>

            {/* Qty: - qty UOM + */}
            <div className="flex items-center justify-center">
              <div className="flex items-center border border-gray-200 dark:border-gray-600 rounded-full">
              <button
                onClick={(e) => { e.stopPropagation(); adjustQuantity(item.id, -1); }}
                className={`${
                  isMobile ? "w-7 h-7" : "w-6 h-6"
                } rounded-full flex items-center justify-center text-gray-400 dark:text-gray-500 hover:bg-red-50 dark:hover:bg-red-900/30 hover:text-red-500 dark:hover:text-red-400 transition-colors`}
                aria-label="Decrease quantity"
              >
                <Minus size={isMobile ? 12 : 10} />
              </button>
              <NumberField
                kind="qty"
                value={item.quantity}
                aria-label="Quantity"
                onCommit={(typed) => { if (typed !== undefined) commitLineQty(typed); }}
                wrapperClassName="border-l border-gray-200 dark:border-gray-600"
                className="w-11 text-center font-semibold text-gray-900 dark:text-white text-sm py-0.5 bg-transparent focus:outline-none focus:bg-gray-50 dark:focus:bg-gray-700"
              />
              {/* Clicking the unit flips to the next one; the full UOM picker is in the expanded panel. */}
              <button
                type="button"
                onClick={cycleUom}
                className="max-w-[2.5rem] truncate px-1 text-[10px] font-medium text-gray-500 dark:text-gray-400 border-r border-gray-200 dark:border-gray-600 hover:text-beveren-600 dark:hover:text-beveren-400"
                title={item.uom ? `Unit: ${item.uom} (click for the next unit)` : "Change unit"}
              >
                {item.uom || "Nos"}
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); adjustQuantity(item.id, 1); }}
                className={`${
                  isMobile ? "w-7 h-7" : "w-6 h-6"
                } rounded-full flex items-center justify-center text-gray-400 dark:text-gray-500 hover:bg-green-50 dark:hover:bg-green-900/30 hover:text-green-600 dark:hover:text-green-400 transition-colors`}
                aria-label="Increase quantity"
              >
                <Plus size={isMobile ? 12 : 10} />
              </button>
              </div>
            </div>

            {/* Rate */}
            <p className="@max-[20rem]:hidden text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums whitespace-nowrap">
              {formatCurrencyWithSymbol(discountedPrice, currency_symbol)}
            </p>

            {/* Total */}
            <div className="text-right tabular-nums">
              {discountedTotal !== originalTotal ? (
                <p className="text-[10px] leading-tight text-gray-400 dark:text-gray-500 line-through whitespace-nowrap">
                  {formatCurrencyWithSymbol(originalTotal, currency_symbol)}
                </p>
              ) : null}
              <p
                className={`text-beveren-600 dark:text-beveren-400 font-semibold whitespace-nowrap ${
                  isMobile ? "text-sm" : "text-sm"
                }`}
              >
                {formatCurrencyWithSymbol(discountedTotal !== originalTotal ? discountedTotal : amount, currency_symbol)}
              </p>
            </div>
          </div>
        </div>

        {isExpanded ? (
          <div className="border-t border-gray-200 dark:border-gray-700 px-3 pt-3 pb-3 bg-gray-50 dark:bg-gray-900/30 flex flex-col gap-3 text-sm">
            {/* Compact edit row: the four things a cashier changes most. */}
            <div className="grid grid-cols-[1fr_1.15fr_1.35fr_1fr] gap-1.5">
              <div className="min-w-0">
                <label className={PANEL_LABEL}>Qty</label>
                <NumberField
                  kind="qty"
                  value={item.quantity}
                  aria-label="Quantity"
                  onCommit={(n) => { if (n !== undefined) onUpdateQuantity(item.id, Math.max(0, n)); }}
                  className={PANEL_INPUT}
                />
              </div>
              <div className="min-w-0">
                <UOMSelectField
                  labelClassName={PANEL_LABEL}
                  buttonClassName="h-9 px-2 text-sm"
                  item={item}
                  onUOMChange={onUOMChange}
                  isMobile={isMobile}
                  selectedCustomer={selectedCustomer}
                />
              </div>
              <div className="min-w-0">
                <label className={PANEL_LABEL}>Rate <span className="normal-case tracking-normal">{hasExclusiveTax ? "excl." : "incl."}</span></label>
                <RatePriceField
                  item={item}
                  warehouse={warehouse}
                  allowRateChange={!!posDetails?.allow_rate_change}
                  allowPriceLists={allowPriceListSwitching}
                  activeList={itemDiscount.selectedPriceList}
                  currencySymbol={currency_symbol || ""}
                  onPick={(p) => ("priceList" in p ? handleLinePriceListChange(p.priceList) : commitPanelRate(p.rate, String(p.rate)))}
                  value={roundCurrency(panelRate)}
                  aria-label={hasExclusiveTax ? "Rate (Excl. Tax)" : "Rate (Incl. Tax)"}
                  readOnly={!posDetails?.allow_rate_change}
                  onCommit={commitPanelRate}
                  className={`${PANEL_INPUT} ${showNegativeMarginWarning ? "border-red-400 dark:border-red-600" : ""}`}
                />
              </div>
              <div className="min-w-0">
                <label className={PANEL_LABEL}>Disc %</label>
                <NumberField
                  kind="percent"
                  value={localDiscountPct}
                  blankZero
                  placeholder="0.00"
                  aria-label="Discount (%)"
                  readOnly={!posDetails?.allow_discount_change}
                  onCommit={(n, text) => { if (n !== undefined || text === "") handleDiscountPercentageChange(n ?? 0); }}
                  className={PANEL_INPUT}
                />
              </div>
            </div>

            {/* Ledger: label left, value right, so long numbers have room. */}
            <div className="flex flex-col gap-2">
              <div className={LEDGER_ROW}>
                <span className={LEDGER_LABEL}>Discount each</span>
                <div className="flex items-center gap-2 min-w-0">
                  {itemDiscount.discountAmount > 0 ? (
                    <span className="text-xs text-gray-500 dark:text-gray-400 tabular-nums whitespace-nowrap">
                      = {formatNumber(itemDiscount.discountAmount * item.quantity, "money")} on line
                    </span>
                  ) : null}
                  <NumberField
                    kind="money"
                    value={itemDiscount.discountAmount}
                    blankZero
                    placeholder="0.00"
                    aria-label="Discount Amount"
                    readOnly={!posDetails?.allow_discount_change}
                    onCommit={(n, text) => { if (n !== undefined || text === "") handleDiscountAmountChange(n ?? 0); }}
                    wrapperClassName="w-28"
                    className={PANEL_INPUT}
                  />
                </div>
              </div>

              {/* With quick-switch pills on, the pills under the row are the price list selector. */}
              {allowPriceListSwitching && !quickSwitchPrice && fullItemData?.price_lists?.length ? (
                <div className={LEDGER_ROW}>
                  <label htmlFor={`line-price-list-${itemId}`} className={LEDGER_LABEL}>Price list</label>
                  <select
                    id={`line-price-list-${itemId}`}
                    value={itemDiscount.selectedPriceList || ""}
                    onChange={(event) => handleLinePriceListChange(event.target.value)}
                    disabled={!posDetails?.allow_rate_change}
                    className="h-9 min-w-0 max-w-[60%] px-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md focus:ring-2 focus:ring-beveren-500 focus:border-transparent bg-white dark:bg-gray-800 text-gray-900 dark:text-white disabled:opacity-60"
                  >
                    <option value="">Use order price list</option>
                    {fullItemData.price_lists
                      .filter((priceList) => !priceList.uom || priceList.uom === item.uom)
                      .map((priceList) => (
                        <option key={`${priceList.price_list}-${priceList.uom || ""}-${priceList.rate}`} value={priceList.price_list}>
                          {priceList.price_list}
                        </option>
                      ))}
                  </select>
                </div>
              ) : null}

              {totalTaxRate > 0 ? (
                <div className={LEDGER_ROW}>
                  <span className={LEDGER_LABEL}>Tax</span>
                  <span className="tabular-nums text-gray-900 dark:text-white text-right">
                    {formatNumber(totalTaxRate, "percent")}%
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      {hasExclusiveTax
                        ? ` · +${formatNumber(taxAmountPerUnit, "money")} each`
                        : ` · ${formatNumber(discountedPrice, "money")} before tax`}
                    </span>
                  </span>
                </div>
              ) : null}

              {serialBatch}

            </div>

            {/* Line total and what the customer saves against the list. */}
            <div className="border-t border-gray-200 dark:border-gray-700 pt-2 flex flex-col gap-0.5">
              <div className={LEDGER_ROW}>
                <span className={LEDGER_LABEL}>Line total</span>
                <span className="text-lg font-semibold tabular-nums text-gray-900 dark:text-white">
                  {formatCurrencyWithSymbol(amount, currency_symbol)}
                </span>
              </div>
              {originalTotal - discountedTotal > 0 ? (
                <div className="flex justify-between gap-3 text-xs tabular-nums">
                  <span className="text-gray-400 line-through">{formatNumber(originalTotal, "money")}</span>
                  <span className="text-green-700 dark:text-green-400">
                    You save {formatNumber(originalTotal - discountedTotal, "money")}
                    {localDiscountPct > 0 ? ` · ${formatNumber(localDiscountPct, "percent")}% off` : ""}
                  </span>
                </div>
              ) : null}
            </div>

            {/* Facts: stock, weight, cost, margin. */}
            {isLoadingFullData ? (
              <div className="flex items-center gap-2 py-1 text-xs text-gray-500">
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-beveren-500 border-t-transparent" />
                Loading stock and cost…
              </div>
            ) : (
              <div className="border-t border-gray-200 dark:border-gray-700 pt-2 flex flex-col gap-1.5">
                {stockRow}

                {weightRow}

                {costRows[costLabel]}
                {costRows[otherLabel]}

                {marginRows}
              </div>
            )}
          </div>
        ) : (
          <></>
        )}

        {bundleModal}
      </div>

      {pricePopup}

      {descriptionDialog}

      {productModal}
    </>
  );
};

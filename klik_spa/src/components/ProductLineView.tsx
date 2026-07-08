"use client"

import { useState } from "react"
import type { MenuItem } from "../../types"
import ProductTooltip from "./ProductTooltip"
import ProductDetailsModal from "./ProductDetailsModal"

import { formatCurrencyWithSymbol } from "../utils/currency"
import { isItemOutOfStock } from "../utils/stock"

interface ProductLineViewProps {
  items: MenuItem[]
  onAddToCart: (item: MenuItem) => void
  isMobile?: boolean
  scannerOnly?: boolean
  showItemCode?: boolean
  hideImages?: boolean
  focusedIndex?: number
  onItemFocus?: (index: number) => void
  onItemKeyDown?: (index: number, item: MenuItem, e: React.KeyboardEvent<HTMLDivElement>) => void
}

export default function ProductLineView({
  items,
  onAddToCart,
  isMobile = false,
  scannerOnly = false,
  showItemCode = false,
  hideImages = false,
  focusedIndex = -1,
  onItemFocus,
  onItemKeyDown,
}: ProductLineViewProps) {
  const [hoveredItemId, setHoveredItemId] = useState<string | number | null>(null)
  const [hoveredImageId, setHoveredImageId] = useState<string | number | null>(null)
  const [selectedItem, setSelectedItem] = useState<MenuItem | null>(null)
  const [showDetailsModal, setShowDetailsModal] = useState(false)

  const handleInfoClick = (item: MenuItem) => {
    setSelectedItem(item)
    setShowDetailsModal(true)
    setHoveredItemId(null)
  }

  const handleModalClose = () => {
    setShowDetailsModal(false)
    setSelectedItem(null)
  }

  if (items.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <div className="text-6xl mb-4">🔍</div>
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
            No items found
          </h3>
          <p className="text-gray-500 dark:text-gray-400">
            Try adjusting your search or filters
          </p>
        </div>
      </div>
    )
  }
  

  return (
    <>
      <div className={`${isMobile ? "p-4" : "p-1"} bg-gray-50 dark:bg-gray-900`}>
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 mb-2 overflow-visible">
          {!isMobile && (
            <div className="grid grid-cols-12 gap-3 px-3 py-2 bg-gray-50 dark:bg-gray-700 border-b">
              <div className="col-span-6 text-xs font-semibold text-gray-900 dark:text-white">Product</div>
              <div className="col-span-2 text-xs font-semibold text-center text-gray-900 dark:text-white">Rate</div>
              <div className="col-span-2 text-xs font-semibold text-center text-gray-900 dark:text-white">Qty</div>
              <div className="col-span-1 text-xs font-semibold text-center text-gray-900 dark:text-white">UOM</div>
              <div className="col-span-1 text-xs font-semibold text-center text-gray-900 dark:text-white">Action</div>
            </div>
          )}

          <div className="divide-y divide-gray-200 dark:divide-gray-600">
            {items.map((item, rowIndex) => {
              const isServiceItem = item.is_stock_item === false
              const isOutOfStock = isItemOutOfStock(item)
              const isDisabled = isOutOfStock || scannerOnly
              const expectedPrice = Number(item.price_with_vat ?? item.price)
              const basePrice = Number(item.price || 0)
              const showsAdjustedPrice = Math.abs(expectedPrice - basePrice) > 0.004
              const formattedPrice = formatCurrencyWithSymbol(expectedPrice, item.currency_symbol)
              const formattedBasePrice = formatCurrencyWithSymbol(basePrice, item.currency_symbol)
              const taxRate = Number(item.tax_info?.total_tax_rate || 0)
              const taxBadgeLabel = item.tax_info?.has_vat
                ? `VAT ${taxRate.toFixed(taxRate % 1 === 0 ? 0 : 2)}% ${item.tax_info.is_inclusive ? "Incl" : "Excl"}`
                : "No VAT"
              const taxBadgeClass = item.tax_info?.has_vat
                ? item.tax_info.is_inclusive
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-200 dark:border-emerald-700"
                  : "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-200 dark:border-amber-700"
                : "bg-gray-50 text-gray-500 border-gray-200 dark:bg-gray-700/50 dark:text-gray-300 dark:border-gray-600"
              const bundleCount = item.is_product_bundle ? item.bundle_items?.length || 0 : 0
              const variantCount = item.is_variant_template ? item.variant_count || 0 : 0

              const isRowFocused = focusedIndex === rowIndex;
              return (
                <div
                  key={item.id}
                  tabIndex={0}
                  data-product-index={rowIndex}
                  onFocus={() => onItemFocus?.(rowIndex)}
                  onKeyDown={(e) => onItemKeyDown?.(rowIndex, item, e)}
                  className={`relative outline-none ${isMobile ? "px-3 py-2" : "grid grid-cols-12 gap-3 px-3 py-1.5"} transition-colors ${
                    isRowFocused
                      ? "bg-beveren-50 dark:bg-beveren-900/20 ring-inset ring-2 ring-beveren-400/50"
                      : "hover:bg-gray-50 dark:hover:bg-gray-700"
                  } ${!isDisabled && "cursor-pointer"}`}
                  onClick={() => !isDisabled && onAddToCart(item)}
                >
                  {/* Image zoom preview — rendered at row level to escape column clipping */}
                  {hoveredImageId === item.id && item.image && !isMobile && (
                    <div className="absolute left-10 top-1/2 -translate-y-1/2 z-50 pointer-events-none">
                      <div className="rounded-lg overflow-hidden shadow-2xl ring-1 ring-black/10 dark:ring-white/10 bg-white dark:bg-gray-800">
                        <img src={item.image} alt={item.name} className="w-32 h-32 object-cover" />
                      </div>
                    </div>
                  )}
                  <div className={`${isMobile ? "flex items-center gap-2" : "col-span-6 flex items-center gap-2"}`}>
                    {!hideImages && (
                      item.image ? (
                        <div
                          className="flex-shrink-0"
                          onMouseEnter={() => !isMobile && setHoveredImageId(item.id)}
                          onMouseLeave={() => !isMobile && setHoveredImageId(null)}
                        >
                          <img
                            src={item.image}
                            alt={item.name}
                            className={`rounded object-cover ${isMobile ? "w-8 h-8" : "w-7 h-7"} ${isDisabled ? "opacity-60" : ""}`}
                          />
                        </div>
                      ) : (
                        <div className={`flex-shrink-0 rounded bg-gray-100 dark:bg-gray-700 ${isMobile ? "w-8 h-8" : "w-7 h-7"} ${isDisabled ? "opacity-60" : ""}`} />
                      )
                    )}
                    <div className="flex-1 min-w-0 relative">
                      <div className="flex items-start gap-1">
                        <h3 className={`font-medium text-gray-900 dark:text-white break-words ${isMobile ? "text-xs leading-tight" : "text-sm"} ${isDisabled ? "opacity-60" : ""}`}>
                          {item.name}
                        </h3>
                        <div
                          className="relative inline-block flex-shrink-0 mt-px"
                          onMouseEnter={() => !isMobile && setHoveredItemId(item.id)}
                          onMouseLeave={() => !isMobile && setHoveredItemId(null)}
                        >
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              handleInfoClick(item)
                            }}
                            className={`text-gray-400 hover:text-blue-500 text-sm focus:outline-none transition-colors ${isDisabled ? "opacity-60" : ""}`}
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
                          </button>
                          {hoveredItemId === item.id && !isMobile && (
                            <ProductTooltip
                              item={item}
                              onClose={() => setHoveredItemId(null)}
                              onViewDetails={() => handleInfoClick(item)}
                            />
                          )}
                        </div>
                      </div>
                      {showItemCode && (
                        <p className={`text-gray-600 dark:text-gray-300 break-words ${isMobile ? "text-xs leading-tight" : "text-xs"} ${isDisabled ? "opacity-60" : ""}`}>
                          {item.item_code || item.id}
                        </p>
                      )}
                      <div className={`flex flex-wrap items-center gap-1 mt-0.5 ${isDisabled ? "opacity-60" : ""}`}>
                        <span className="inline-flex items-center rounded border border-gray-200 dark:border-gray-600 bg-gray-100 dark:bg-gray-700/60 px-1.5 py-px text-[9px] font-medium leading-none text-gray-500 dark:text-gray-400">
                          {item.category}
                        </span>
                        <span
                          className={`inline-flex items-center rounded border px-1.5 py-px text-[9px] font-semibold leading-none ${taxBadgeClass}`}
                          title={item.tax_info?.item_tax_template || item.tax_info?.source || taxBadgeLabel}
                        >
                          {taxBadgeLabel}
                        </span>
                        {item.is_product_bundle && (
                          <span className="inline-flex items-center rounded border border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/30 px-1.5 py-px text-[9px] font-medium leading-none text-amber-700 dark:text-amber-300">
                            Bundle · {bundleCount}
                          </span>
                        )}
                        {item.is_variant_template && (
                          <span className="inline-flex items-center rounded border border-sky-200 dark:border-sky-700 bg-sky-50 dark:bg-sky-900/30 px-1.5 py-px text-[9px] font-medium leading-none text-sky-700 dark:text-sky-300">
                            {variantCount} variants
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {isMobile ? (
                    <div className="mt-2 grid grid-cols-12 gap-2 items-end">
                      <div className={`col-span-4 ${isDisabled ? "opacity-60" : ""}`}>
                        <p className="text-[10px] text-gray-500 dark:text-gray-400">Rate</p>
                        <p className="font-semibold text-beveren-600 dark:text-beveren-400 text-xs">
                          {formattedPrice}
                        </p>
                        {showsAdjustedPrice && (
                          <p className="text-[12px] font-medium leading-tight text-gray-600 dark:text-gray-300">
                            Base {formattedBasePrice}
                          </p>
                        )}
                      </div>

                      <div className={`col-span-3 text-center ${isDisabled ? "opacity-60" : ""}`}>
                        <p className="text-[10px] text-gray-500 dark:text-gray-400">Qty</p>
                        <p className={`font-medium text-xs ${
                          isOutOfStock ? "text-red-600 dark:text-red-400" : "text-gray-900 dark:text-white"
                        }`}>
                          {isOutOfStock ? "0" : item.is_variant_template ? variantCount : item.is_product_bundle ? item.available : isServiceItem ? "Service" : item.available}
                        </p>
                      </div>

                      <div className="col-span-5 flex justify-end">
                        {isDisabled ? (
                          <span className="text-gray-400 dark:text-gray-500 text-xs opacity-60">
                            {isOutOfStock ? "Out" : "Scan"}
                          </span>
                        ) : (
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              onAddToCart(item)
                            }}
                            className="bg-slate-100 dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 font-medium rounded-md transition-colors hover:bg-slate-200 dark:hover:bg-slate-600 hover:border-slate-400 dark:hover:border-slate-500 px-3 py-1.5 text-xs"
                          >
                            Add
                          </button>
                        )}
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className={`col-span-2 flex items-center justify-center ${isDisabled ? "opacity-60" : ""}`}>
                        <div className="text-center">
                          <span className="block font-semibold text-beveren-600 dark:text-beveren-400 text-sm">
                            {formattedPrice}
                          </span>
                          {showsAdjustedPrice && (
                            <span className="block text-[10px] font-medium leading-tight text-gray-600 dark:text-gray-300">
                              Base {formattedBasePrice}
                            </span>
                          )}
                        </div>
                      </div>

                      <div className={`col-span-2 flex items-center justify-center ${isDisabled ? "opacity-60" : ""}`}>
                        <span className={`font-medium text-sm ${
                          isOutOfStock ? "text-red-600 dark:text-red-400" : "text-gray-900 dark:text-white"
                        }`}>
                          {isOutOfStock ? "0" : item.is_variant_template ? variantCount : item.is_product_bundle ? item.available : isServiceItem ? "Service" : item.available}
                        </span>
                      </div>

                      <div className={`col-span-1 flex items-center justify-center ${isDisabled ? "opacity-60" : ""}`}>
                        <span className="text-xs text-gray-500 dark:text-gray-400">
                          {item.uom || "Nos"}
                        </span>
                      </div>

                      <div className="col-span-1 flex items-center justify-center">
                        {isDisabled ? (
                          <span className="text-gray-400 dark:text-gray-500 text-xs opacity-60">
                            {isOutOfStock ? "Out" : "Scan"}
                          </span>
                        ) : (
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              onAddToCart(item)
                            }}
                            className="bg-slate-100 dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 font-medium rounded-md transition-colors hover:bg-slate-200 dark:hover:bg-slate-600 hover:border-slate-400 dark:hover:border-slate-500 px-2 py-1 text-xs"
                          >
                            Add
                          </button>
                        )}
                      </div>
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {showDetailsModal && selectedItem && (
        <ProductDetailsModal
          item={selectedItem}
          onClose={handleModalClose}
        />
      )}
    </>
  )
}

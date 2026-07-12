import type { BackendTaxPreview, Calculations } from "./types";

interface TaxSectionProps {
  selectedCustomer: any;
  invoiceSubmitted: boolean;
  isProcessingPayment: boolean;
  taxPin: string;
  onTaxPinChange: (pin: string) => void;
  calculations: Calculations;
  displayCurrencySymbol: string;
  backendTaxPreview: BackendTaxPreview | null;
  isTaxPreviewLoading: boolean;
  taxPreviewError: string | null;
  allowDiscountChange: boolean;
  orderDiscountAmount: number;
  orderDiscountPercentInput: number;
  onOrderDiscountAmountChange: (value: number) => void;
  onOrderDiscountPercentChange: (value: number) => void;
}

export default function TaxSection({
  selectedCustomer,
  invoiceSubmitted,
  isProcessingPayment,
  taxPin,
  onTaxPinChange,
  allowDiscountChange,
  orderDiscountAmount,
  orderDiscountPercentInput,
  onOrderDiscountAmountChange,
  onOrderDiscountPercentChange,
}: TaxSectionProps) {
  return (
    <div>
      <div className="flex gap-3 items-start">
        <div className="min-w-0 flex-1">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            {invoiceSubmitted ? "Customer Tax ID" : "Customer Tax ID (optional)"}
          </label>
          {selectedCustomer?.isWalkin === 1 ? (
            <input
              type="text"
              value={taxPin}
              onChange={(e) => onTaxPinChange(e.target.value.toUpperCase())}
              onBlur={() => onTaxPinChange(taxPin.trim().toUpperCase())}
              placeholder="A123456789P"
              disabled={invoiceSubmitted || isProcessingPayment}
              className={`w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white uppercase tracking-widest ${invoiceSubmitted || isProcessingPayment ? "cursor-not-allowed opacity-50" : ""}`}
            />
          ) : (
            <input
              type="text"
              value={selectedCustomer?.taxId || ""}
              placeholder="No Tax ID on file"
              disabled
              readOnly
              className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-white uppercase tracking-widest cursor-not-allowed opacity-75"
            />
          )}
        </div>
        {allowDiscountChange && (
          <div className="w-1/3 flex-shrink-0">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Discount</label>
            <div className="grid grid-cols-2 gap-1.5">
              <input
                type="number"
                min="0"
                step="0.01"
                placeholder="Amount"
                value={orderDiscountAmount || ""}
                onChange={(e) => onOrderDiscountAmountChange(Number(e.target.value || 0))}
                disabled={invoiceSubmitted || isProcessingPayment}
                className={`w-full px-2 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white ${invoiceSubmitted || isProcessingPayment ? "cursor-not-allowed opacity-50" : ""}`}
              />
              <input
                type="number"
                min="0"
                max="100"
                step="0.1"
                placeholder="%"
                value={orderDiscountPercentInput || ""}
                onChange={(e) => onOrderDiscountPercentChange(Number(e.target.value || 0))}
                disabled={invoiceSubmitted || isProcessingPayment}
                className={`w-full px-2 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white ${invoiceSubmitted || isProcessingPayment ? "cursor-not-allowed opacity-50" : ""}`}
              />
            </div>
          </div>
        )}
      </div>
      {/* <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Sales & Tax Charges</label>
        <select
          value={selectedSalesTaxCharges}
          onChange={(e) => onTaxChange(e.target.value)}
          disabled={invoiceSubmitted || isProcessingPayment}
          className={`w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white ${invoiceSubmitted || isProcessingPayment ? "cursor-not-allowed opacity-50" : ""}`}
        >
          {salesTaxCharges.map((tax) => (
            <option key={tax.id} value={tax.id}>
              {tax.name} ({tax.rate}% {tax.is_inclusive ? "Incl." : "Excl."})
            </option>
          ))}
        </select>
      </div> */}
    </div>
  );
}

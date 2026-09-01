import { CheckCircle } from "lucide-react";
import type { PaymentMethod } from "./types";

interface PaymentMethodsProps {
  paymentMethods: PaymentMethod[];
  invoiceSubmitted: boolean;
  isProcessingPayment: boolean;
  onAmountChange: (methodId: string, amount: string) => void;
  onAutoFill: (methodId: string) => void;
  setActiveMethodId: (id: string | null) => void;
}

export default function PaymentMethods({
  paymentMethods,
  invoiceSubmitted,
  isProcessingPayment,
  onAmountChange,
  onAutoFill,
  setActiveMethodId,
}: PaymentMethodsProps) {
  return (
    <div>
      <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-3">Payment Methods</h3>
      {/* Grid instead of a horizontally-scrolling flex row: every payment method
          (Cash, M-Pesa, Cheque, Credit, Credit Card, ...) wraps onto as many rows
          as needed at a fixed column width, so the cashier never has to drag/scroll
          sideways to find one -- everything stays on the page. Card padding and
          font sizes are trimmed to keep the extra rows this creates from costing
          too much vertical space. */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {paymentMethods.map((method) => (
          <div
            key={method.id}
            className={`border border-gray-200 dark:border-gray-700 rounded-lg p-2.5 hover:border-beveren-300 transition-colors ${invoiceSubmitted || isProcessingPayment ? "bg-gray-50 dark:bg-gray-800" : ""}`}
          >
            <div className="flex items-center justify-between gap-1.5 mb-1.5">
              <div className="flex items-center gap-1.5 min-w-0">
                <div className={`w-6 h-5 shrink-0 rounded-md ${method.color} text-white flex items-center justify-center`}>
                  <div className="scale-[0.6]">{method.icon}</div>
                </div>
                <p className="font-medium text-gray-900 dark:text-white text-xs truncate">{method.name}</p>
              </div>
              <button
                onClick={() => onAutoFill(method.id)}
                disabled={invoiceSubmitted || isProcessingPayment}
                className={`p-0.5 rounded shrink-0 ${invoiceSubmitted || isProcessingPayment ? "cursor-not-allowed opacity-50" : "hover:bg-beveren-100 text-beveren-600"}`}
                title="Auto-fill with grand total"
              >
                <CheckCircle size={14} />
              </button>
            </div>
            <input
              type="number"
              min="0"
              step="0.01"
              value={method.amount || ""}
              onChange={(e) => {
                setActiveMethodId(method.id);
                const inputValue = e.target.value;
                const numValue = inputValue === "" ? 0 : parseFloat(inputValue);
                onAmountChange(method.id, isNaN(numValue) ? "0" : numValue.toString());
              }}
              onBlur={(e) => {
                setActiveMethodId(method.id);
                const numValue = parseFloat(e.target.value);
                if (!isNaN(numValue)) {
                  onAmountChange(method.id, parseFloat(numValue.toFixed(2)).toString());
                }
              }}
              placeholder="0.00"
              disabled={invoiceSubmitted || isProcessingPayment}
              className={`w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded-md focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm ${invoiceSubmitted || isProcessingPayment ? "cursor-not-allowed opacity-50" : ""}`}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
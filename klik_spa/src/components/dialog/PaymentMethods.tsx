import type { ReactNode } from "react";
import { Circle, CheckCircle2 } from "lucide-react";
import type { PaymentMethod } from "./types";
import { isReferenceMethod } from "./paymentIcons";

interface PaymentMethodsProps {
  paymentMethods: PaymentMethod[];
  invoiceSubmitted: boolean;
  isProcessingPayment: boolean;
  onAmountChange: (methodId: string, amount: string) => void;
  onToggle: (methodId: string) => void;
  onReferenceChange: (methodId: string, value: string) => void;
  setActiveMethodId: (id: string | null) => void;
  references: Record<string, string>;
  headerRight?: ReactNode;
}

export default function PaymentMethods({
  paymentMethods,
  invoiceSubmitted,
  isProcessingPayment,
  onAmountChange,
  onToggle,
  onReferenceChange,
  setActiveMethodId,
  references,
  headerRight,
}: PaymentMethodsProps) {
  const disabled = invoiceSubmitted || isProcessingPayment;

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Payment Methods</h3>
        {headerRight}
      </div>

      <div className="border border-gray-200 dark:border-gray-700 rounded-lg divide-y divide-gray-200 dark:divide-gray-700">
        {paymentMethods.map((method) => {
          const isActive = (method.amount || 0) > 0;
          const showRef = isActive && isReferenceMethod(method.type, method.name);

          return (
            <div
              key={method.id}
              className={`flex flex-wrap items-center gap-3 px-3 py-2 ${disabled ? "bg-gray-50 dark:bg-gray-800" : ""}`}
            >
              <button
                type="button"
                onClick={() => onToggle(method.id)}
                disabled={disabled}
                title="Use this method (fill outstanding)"
                className={`shrink-0 ${disabled ? "cursor-not-allowed opacity-50" : "hover:text-beveren-600"} ${isActive ? "text-beveren-600" : "text-gray-400"}`}
              >
                {isActive ? <CheckCircle2 size={20} /> : <Circle size={20} />}
              </button>

              <div className={`w-7 h-6 rounded-md ${method.color} text-white flex items-center justify-center shrink-0`}>
                <div className="scale-75">{method.icon}</div>
              </div>

              <span className="flex-1 min-w-[7rem] font-medium text-gray-900 dark:text-white text-sm truncate">
                {method.name}
              </span>

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
                disabled={disabled}
                className={`w-28 shrink-0 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm text-right ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
              />

              {showRef && (
                <input
                  type="text"
                  value={references[method.id] || ""}
                  onChange={(e) => onReferenceChange(method.id, e.target.value)}
                  placeholder="Reference / Cheque no."
                  disabled={disabled}
                  className={`w-full sm:w-40 shrink-0 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

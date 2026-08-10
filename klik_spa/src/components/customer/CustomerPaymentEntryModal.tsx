import { useEffect, useMemo, useState } from "react";
import { Banknote, Check, Loader2, X } from "lucide-react";
import { toast } from "react-toastify";
import type { Customer } from "../../types/customer";
import { usePaymentModes } from "../../hooks/usePaymentModes";
import { usePOSProfileStore } from "../../stores/posProfileStore";
import { createCustomerPaymentEntry } from "../../services/paymentEntry";
import type { ReceivableInvoice } from "../../services/paymentEntry";
import { formatCurrencyWithSymbol } from "../../utils/currency";
import { extractErrorFromException } from "../../utils/errorExtraction";
import {
  allocateOldestFirst,
  allocatedAmountFor,
  splitSingleInvoice,
  sumOutstanding,
} from "../../utils/allocateOldestFirst";
import { defaultReceiveMode, requiresReference, selectableReceiveModes } from "../../utils/receiveModes";
import { formatAmountInput, stripAmountInput } from "../../utils/amountInput";

interface CustomerPaymentEntryModalProps {
  customer: Customer;
  salesInvoiceName?: string;
  outstandingAmount?: number;
  defaultAmount?: number;
  invoiceCurrency?: string;
  allocationTargets?: ReceivableInvoice[];
  onClose: () => void;
  onCreated?: (paymentEntryName: string) => void;
}

export default function CustomerPaymentEntryModal({
  customer,
  salesInvoiceName,
  outstandingAmount,
  defaultAmount,
  invoiceCurrency,
  allocationTargets,
  onClose,
  onCreated,
}: CustomerPaymentEntryModalProps) {
  const { posDetails, currencySymbol } = usePOSProfileStore();
  const posProfileName = posDetails?.name || "";
  const { modes, isLoading, error } = usePaymentModes(posProfileName);

  const [amount, setAmount] = useState(defaultAmount ? String(defaultAmount) : "");
  const [modeOfPayment, setModeOfPayment] = useState("");
  const [referenceNo, setReferenceNo] = useState("");
  const [referenceDate, setReferenceDate] = useState("");
  const [remarks, setRemarks] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectedInvoices, setSelectedInvoices] = useState<Set<string>>(new Set());

  const selectableModes = useMemo(() => selectableReceiveModes(modes), [modes]);
  const defaultMode = useMemo(() => defaultReceiveMode(modes), [modes]);
  const referenceRequired = useMemo(
    () => requiresReference(modes, modeOfPayment),
    [modes, modeOfPayment]
  );

  useEffect(() => {
    if (!modeOfPayment && defaultMode) {
      setModeOfPayment(defaultMode);
    }
  }, [defaultMode, modeOfPayment]);

  // Identity-independent key: a parent that rebuilds the array each render would otherwise
  // retrigger the effects below forever.
  const allocationKey = useMemo(
    () => (allocationTargets || []).map((invoice) => invoice.name).join("|"),
    [allocationTargets]
  );

  // Every invoice starts selected, so the modal opens the way it always has: amount
  // pre-filled with the total and everything allocated oldest-first.
  useEffect(() => {
    setSelectedInvoices(new Set((allocationTargets || []).map((invoice) => invoice.name)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allocationKey]);

  // Selection drives the amount. Typing over it is allowed — the excess simply becomes an
  // advance — but the next selection change resyncs, so there is no hidden edited state.
  useEffect(() => {
    if (!allocationTargets?.length) return;
    const total = sumOutstanding(
      allocationTargets.filter((invoice) => selectedInvoices.has(invoice.name))
    );
    setAmount(total ? String(total) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedInvoices, allocationKey]);

  const numericAmount = Number(amount);
  const allocationSplit = useMemo(() => {
    if (!allocationTargets?.length) return null;
    const chosen = allocationTargets.filter((invoice) => selectedInvoices.has(invoice.name));
    return allocateOldestFirst(numericAmount, chosen);
  }, [allocationTargets, selectedInvoices, numericAmount]);
  const singleSplit = useMemo(
    () =>
      salesInvoiceName && outstandingAmount !== undefined
        ? splitSingleInvoice(numericAmount, Number(outstandingAmount))
        : null,
    [salesInvoiceName, outstandingAmount, numericAmount]
  );
  const missingReference = referenceRequired && !referenceNo.trim();
  const canSubmit =
    numericAmount > 0 && Boolean(modeOfPayment) && !missingReference && !isSubmitting;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;

    setIsSubmitting(true);
    try {
      const result = await createCustomerPaymentEntry({
        customer: customer.id,
        amount: Math.round(numericAmount * 100) / 100,
        mode_of_payment: modeOfPayment,
        sales_invoice: allocationSplit ? undefined : salesInvoiceName,
        allocated_amount: !allocationSplit && salesInvoiceName ? singleSplit?.allocated : undefined,
        allocations: allocationSplit?.allocations.length ? allocationSplit.allocations : undefined,
        reference_no: referenceNo.trim() || undefined,
        reference_date: referenceDate || undefined,
        remarks: remarks.trim() || undefined,
      });

      toast.success(`Payment received: ${result.name}`);
      onCreated?.(result.name);
      onClose();
    } catch (err) {
      toast.error(extractErrorFromException(err, "Failed to receive customer payment"));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-lg rounded-lg bg-white shadow-xl dark:bg-gray-900"
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4 dark:border-gray-700">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-beveren-100 text-beveren-700 dark:bg-beveren-900 dark:text-beveren-300">
              <Banknote size={20} />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Receive Payment</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                {customer.name}
                {salesInvoiceName ? ` • ${salesInvoiceName}` : ""}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4 px-5 py-5">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
              {error}
            </div>
          )}

          {salesInvoiceName && outstandingAmount !== undefined && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
              Outstanding: {formatCurrencyWithSymbol(outstandingAmount, invoiceCurrency || currencySymbol)}
            </div>
          )}

          {allocationSplit && (
            <div className="rounded-lg border border-gray-200 dark:border-gray-700">
              <div className="border-b border-gray-200 px-3 py-2 text-xs font-medium uppercase tracking-wider text-gray-500 dark:border-gray-700 dark:text-gray-400">
                Allocation
              </div>
              <div className="max-h-[17rem] divide-y divide-gray-200 overflow-y-auto dark:divide-gray-700">
                {allocationTargets?.map((target) => {
                  const isSelected = selectedInvoices.has(target.name);
                  // A selected row shows what it will actually receive — which is zero when
                  // the amount ran out before reaching it. An unselected row shows what the
                  // invoice owes, as a reference figure. Never show an outstanding balance
                  // in a selected row: it reads as "this will be paid" when it will not.
                  const allocated = allocatedAmountFor(allocationSplit, target.name);
                  return (
                    <button
                      key={target.name}
                      type="button"
                      aria-pressed={isSelected}
                      onClick={() =>
                        setSelectedInvoices((current) => {
                          const next = new Set(current);
                          if (next.has(target.name)) next.delete(target.name);
                          else next.add(target.name);
                          return next;
                        })
                      }
                      className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm focus:outline-none focus:ring-2 focus:ring-inset focus:ring-beveren-500 ${
                        isSelected
                          ? "bg-beveren-50 dark:bg-beveren-950/30"
                          : "opacity-60 hover:bg-gray-50 dark:hover:bg-gray-800"
                      }`}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <span
                          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                            isSelected
                              ? "border-beveren-600 bg-beveren-600 text-white"
                              : "border-gray-300 dark:border-gray-600"
                          }`}
                          aria-hidden="true"
                        >
                          {isSelected ? <Check size={12} /> : null}
                        </span>
                        <span className="min-w-0 truncate text-gray-900 dark:text-white">
                          {target.name}
                          {target.due_date ? (
                            <span className="ml-2 text-xs text-gray-500 dark:text-gray-400">
                              due {target.due_date}
                            </span>
                          ) : null}
                        </span>
                      </span>
                      <span
                        className={`shrink-0 font-medium ${
                          isSelected && allocated === 0
                            ? "text-gray-400 dark:text-gray-500"
                            : "text-gray-900 dark:text-white"
                        }`}
                      >
                        {formatCurrencyWithSymbol(
                          isSelected ? allocated : target.outstanding,
                          invoiceCurrency || currencySymbol
                        )}
                      </span>
                    </button>
                  );
                })}
                {allocationSplit && allocationSplit.unallocated > 0 && (
                  <div className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <span className="text-gray-500 dark:text-gray-400">Unallocated (advance)</span>
                    <span className="shrink-0 font-medium text-green-700 dark:text-green-300">
                      {formatCurrencyWithSymbol(allocationSplit.unallocated, invoiceCurrency || currencySymbol)}
                    </span>
                  </div>
                )}
                {selectedInvoices.size === 0 && (
                  <div className="px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
                    No invoice selected — the whole amount will be received as an advance.
                  </div>
                )}
                {selectedInvoices.size > 0 && numericAmount <= 0 && (
                  <div className="px-3 py-2 text-sm text-gray-500 dark:text-gray-400">
                    Enter an amount to allocate.
                  </div>
                )}
              </div>
            </div>
          )}

          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Amount
            </label>
            {/* type="text", not "number": a number input cannot render thousand separators.
                State stays raw — Number("349,903") is NaN — and only the display is grouped. */}
            <input
              type="text"
              inputMode="decimal"
              value={formatAmountInput(amount)}
              onChange={(event) => setAmount(stripAmountInput(event.target.value))}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
              autoFocus
            />
            {singleSplit && singleSplit.unallocated > 0 && (
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                {formatCurrencyWithSymbol(singleSplit.allocated, invoiceCurrency || currencySymbol)}{" "}
                settles this invoice;{" "}
                <span className="font-medium text-green-700 dark:text-green-300">
                  {formatCurrencyWithSymbol(singleSplit.unallocated, invoiceCurrency || currencySymbol)}
                </span>{" "}
                will be left as an unallocated advance.
              </p>
            )}
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                Mode of Payment
              </label>
              <select
                value={modeOfPayment}
                onChange={(event) => setModeOfPayment(event.target.value)}
                disabled={isLoading || selectableModes.length === 0}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 disabled:bg-gray-100 dark:border-gray-600 dark:bg-gray-800 dark:text-white dark:disabled:bg-gray-800/60"
              >
                <option value="">Select payment mode</option>
                {selectableModes.map((mode) => (
                  <option key={mode.mode_of_payment} value={mode.mode_of_payment}>
                    {mode.mode_of_payment}
                  </option>
                ))}
              </select>
              {!isLoading && !error && selectableModes.length === 0 && (
                <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
                  No payment mode on this POS Profile can be used to receive a payment. M-Pesa
                  modes are excluded until the receive integration is built.
                </p>
              )}
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                Reference No.
                {referenceRequired && <span className="ml-1 text-red-500">*</span>}
              </label>
              <input
                type="text"
                value={referenceNo}
                onChange={(event) => setReferenceNo(event.target.value)}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
              />
              {referenceRequired && !referenceNo.trim() && (
                <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
                  {modeOfPayment} is a bank account — a reference number is required.
                </p>
              )}
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                Reference Date
              </label>
              <input
                type="date"
                value={referenceDate}
                onChange={(event) => setReferenceDate(event.target.value)}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
              />
            </div>
          </div>

          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Remarks
            </label>
            <textarea
              rows={3}
              value={remarks}
              onChange={(event) => setRemarks(event.target.value)}
              className="w-full resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-gray-200 px-5 py-4 dark:border-gray-700">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="flex items-center gap-2 rounded-lg bg-beveren-600 px-4 py-2 text-sm font-medium text-white hover:bg-beveren-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {isSubmitting ? <Loader2 size={16} className="animate-spin" /> : <Banknote size={16} />}
            <span>Receive Payment</span>
          </button>
        </div>
      </form>
    </div>
  );
}

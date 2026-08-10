import { Fragment, useState } from "react";
import { Banknote, ChevronDown, ChevronRight, FileText, Loader2 } from "lucide-react";
import type { CustomerReceivable, ReceivableInvoice } from "../../services/paymentEntry";
import { formatCurrencyWithSymbol } from "../../utils/currency";

interface CustomerReceivablesTableProps {
  receivables: CustomerReceivable[];
  currency: string;
  isLoading: boolean;
  error: string | null;
  onReceiveCustomer: (receivable: CustomerReceivable) => void;
  onReceiveInvoice: (receivable: CustomerReceivable, invoice: ReceivableInvoice) => void;
  onStatement?: (receivable: CustomerReceivable) => void;
  // True only when the backend explicitly returned `degraded: true` for this fetch — the
  // figures below are Sales-Invoice-only (gross of advances, no journal entries).
  degraded?: boolean;
  degradedReason?: string | null;
}

const statusBadge = (status?: string | null) => {
  const base = "px-2 py-1 rounded-full text-xs font-medium";
  switch ((status || "").toLowerCase()) {
    case "paid":
      return `${base} bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400`;
    case "unpaid":
      return `${base} bg-yellow-100 text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400`;
    case "partly paid":
      return `${base} bg-orange-100 text-orange-800 dark:bg-orange-900/20 dark:text-orange-400`;
    case "overdue":
      return `${base} bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400`;
    default:
      return `${base} bg-gray-100 text-gray-800 dark:bg-gray-900/20 dark:text-gray-400`;
  }
};

const dash = (value: number, currency: string) =>
  value ? formatCurrencyWithSymbol(value, currency) : "—";

export default function CustomerReceivablesTable({
  receivables,
  currency,
  isLoading,
  error,
  onReceiveCustomer,
  onReceiveInvoice,
  onStatement,
  degraded,
  degradedReason,
}: CustomerReceivablesTableProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (customer: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(customer)) next.delete(customer);
      else next.add(customer);
      return next;
    });
  };

  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-10 text-center text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
        <span className="inline-flex items-center gap-2">
          <Loader2 size={18} className="animate-spin" />
          Loading customer balances...
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
        {error}
      </div>
    );
  }

  if (receivables.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-10 text-center text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
        No customers with an outstanding balance.
      </div>
    );
  }

  const renderInvoices = (receivable: CustomerReceivable) => (
    <div className="bg-gray-50 px-4 py-3 dark:bg-gray-900/40">
      {receivable.invoices.length === 0 ? (
        <div className="text-sm text-gray-500 dark:text-gray-400">
          No outstanding invoices — this balance is an unallocated advance.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400">
                <th className="px-3 py-2">Invoice</th>
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2 text-right">Grand Total</th>
                <th className="px-3 py-2 text-right">Paid</th>
                <th className="px-3 py-2 text-right">Outstanding</th>
                <th className="px-3 py-2">Due Date</th>
                <th className="px-3 py-2 text-right">Days Overdue</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {receivable.invoices.map((invoice) => (
                <tr key={invoice.name}>
                  <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-900 dark:text-white">
                    {invoice.name}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-gray-600 dark:text-gray-300">
                    {invoice.posting_date}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right text-gray-900 dark:text-white">
                    {formatCurrencyWithSymbol(invoice.grand_total, currency)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right text-gray-600 dark:text-gray-300">
                    {formatCurrencyWithSymbol(invoice.paid, currency)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right font-medium text-red-700 dark:text-red-300">
                    {formatCurrencyWithSymbol(invoice.outstanding, currency)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-gray-600 dark:text-gray-300">
                    {invoice.due_date || "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right text-gray-600 dark:text-gray-300">
                    {invoice.days_overdue || "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <span className={statusBadge(invoice.status)}>{invoice.status || "—"}</span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => onReceiveInvoice(receivable, invoice)}
                      className="inline-flex items-center gap-2 rounded-lg bg-beveren-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-beveren-700"
                    >
                      <Banknote size={14} />
                      <span>Receive</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {receivable.unallocated_advance > 0 && (
        <div className="mt-3 rounded-lg border border-beveren-300 px-3 py-2 text-sm dark:border-beveren-700">
          Unallocated Advance:{" "}
          <span className="font-semibold text-green-700 dark:text-green-300">
            {formatCurrencyWithSymbol(receivable.unallocated_advance, currency)}
          </span>{" "}
          <span className="text-gray-500 dark:text-gray-400">already netted into Outstanding above</span>
        </div>
      )}
    </div>
  );

  return (
    <>
      {degraded && (
        <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {degradedReason ||
            "Figures exclude advances and journal entries. Totals may be understated if a customer has an unallocated advance."}
        </div>
      )}

      {/* Desktop */}
      <div className="hidden overflow-hidden rounded-lg border border-gray-200 bg-white md:block dark:border-gray-700 dark:bg-gray-800">
        <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-50 dark:bg-gray-700">
              <tr className="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400">
                <th className="px-4 py-3 text-left">Customer</th>
                <th className="px-4 py-3 text-right">Outstanding</th>
                <th className="px-4 py-3 text-right">Current</th>
                <th className="px-4 py-3 text-right">0-30</th>
                <th className="px-4 py-3 text-right">31-60</th>
                <th className="px-4 py-3 text-right">61-90</th>
                <th className="px-4 py-3 text-right">90+</th>
                <th className="px-4 py-3 text-right">Advance</th>
                <th className="px-4 py-3 text-left">Last Payment</th>
                <th className="px-4 py-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-600">
              {receivables.map((receivable) => {
                const isOpen = expanded.has(receivable.customer);
                return (
                  <Fragment key={receivable.customer}>
                    <tr
                      className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700"
                      onClick={() => toggle(receivable.customer)}
                    >
                      <td className="px-4 py-3">
                        <span className="flex items-center gap-2 font-medium text-gray-900 dark:text-white">
                          {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                          {receivable.customer_name}
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-normal text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                            {receivable.invoices.length}
                          </span>
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right font-semibold text-gray-900 dark:text-white">
                        {formatCurrencyWithSymbol(receivable.outstanding, currency)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-600 dark:text-gray-300">
                        {dash(receivable.bucket_current, currency)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-600 dark:text-gray-300">
                        {dash(receivable.bucket_0_30, currency)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-600 dark:text-gray-300">
                        {dash(receivable.bucket_31_60, currency)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-600 dark:text-gray-300">
                        {dash(receivable.bucket_61_90, currency)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-600 dark:text-gray-300">
                        {dash(receivable.bucket_90_plus, currency)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-green-700 dark:text-green-300">
                        {dash(receivable.unallocated_advance, currency)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600 dark:text-gray-300">
                        {receivable.last_payment || "—"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right">
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            onReceiveCustomer(receivable);
                          }}
                          className="inline-flex items-center gap-2 rounded-lg bg-beveren-600 px-3 py-2 text-sm font-medium text-white hover:bg-beveren-700"
                        >
                          <Banknote size={16} />
                          <span>Receive</span>
                        </button>
                        {onStatement && (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              onStatement(receivable);
                            }}
                            className="ml-2 inline-flex items-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
                          >
                            <FileText size={16} />
                            <span>Statement</span>
                          </button>
                        )}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={10} className="p-0">
                          {renderInvoices(receivable)}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Mobile */}
      <div className="space-y-3 md:hidden">
        {receivables.map((receivable) => {
          const isOpen = expanded.has(receivable.customer);
          return (
            <div
              key={receivable.customer}
              className="overflow-hidden rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"
            >
              <button
                type="button"
                onClick={() => toggle(receivable.customer)}
                className="flex w-full items-start justify-between gap-3 px-3 py-3 text-left"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 font-medium text-gray-900 dark:text-white">
                    {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    <span className="truncate">{receivable.customer_name}</span>
                  </div>
                  {receivable.unallocated_advance > 0 && (
                    <div className="mt-1 pl-6 text-xs text-green-700 dark:text-green-300">
                      Advance {formatCurrencyWithSymbol(receivable.unallocated_advance, currency)}
                    </div>
                  )}
                </div>
                <div className="shrink-0 text-right">
                  <div className="font-semibold text-gray-900 dark:text-white">
                    {formatCurrencyWithSymbol(receivable.outstanding, currency)}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">outstanding</div>
                </div>
              </button>
              <div className="border-t border-gray-200 px-3 py-2 dark:border-gray-700">
                <button
                  type="button"
                  onClick={() => onReceiveCustomer(receivable)}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-beveren-600 px-3 py-2 text-sm font-medium text-white hover:bg-beveren-700"
                >
                  <Banknote size={16} />
                  <span>Receive</span>
                </button>
                {onStatement && (
                  <button
                    type="button"
                    onClick={() => onStatement(receivable)}
                    className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
                  >
                    <FileText size={16} />
                    <span>Statement</span>
                  </button>
                )}
              </div>
              {isOpen && renderInvoices(receivable)}
            </div>
          );
        })}
      </div>
    </>
  );
}

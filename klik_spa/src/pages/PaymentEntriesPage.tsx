import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, Banknote, CreditCard, FileText, Loader2, Search, User } from "lucide-react";
import { toast } from "react-toastify";
import BottomNavigation from "../components/BottomNavigation";
import CustomerPaymentEntryModal from "../components/customer/CustomerPaymentEntryModal";
import CustomerReceivablesTable from "../components/customer/CustomerReceivablesTable";
import type { Customer } from "../types/customer";
import {
  getCustomerReceivables,
  getOutstandingSalesInvoices,
  getUnallocatedCustomerPaymentEntries,
  reconcilePaymentEntryWithInvoice,
  type CustomerReceivable,
  type OutstandingSalesInvoice,
  type ReceivableInvoice,
  type UnallocatedCustomerPaymentEntry,
} from "../services/paymentEntry";
import { formatCurrencyWithSymbol } from "../utils/currency";
import { formatDateTime, toSortableTimestamp } from "../utils/time";
import { useTableSort } from "../hooks/useTableSort";
import SortableHeaderButton from "../components/SortableHeaderButton";
import { usePOSProfileStore } from "../stores/posProfileStore";

const getStatusBadge = (status: string) => {
  const baseClasses = "px-2 py-1 rounded-full text-xs font-medium";
  const normalized = status?.toLowerCase() || "";

  switch (normalized) {
    case "paid":
      return `${baseClasses} bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400`;
    case "unpaid":
      return `${baseClasses} bg-yellow-100 text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400`;
    case "partly paid":
      return `${baseClasses} bg-orange-100 text-orange-800 dark:bg-orange-900/20 dark:text-orange-400`;
    case "overdue":
      return `${baseClasses} bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400`;
    case "draft":
      return `${baseClasses} bg-gray-100 text-gray-800 dark:bg-gray-900/20 dark:text-gray-400`;
    case "return":
      return `${baseClasses} bg-purple-100 text-purple-800 dark:bg-purple-900/20 dark:text-purple-400`;
    case "cancelled":
      return `${baseClasses} bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400`;
    default:
      return `${baseClasses} bg-gray-100 text-gray-800 dark:bg-gray-900/20 dark:text-gray-400`;
  }
};

type PaymentTab = "invoice" | "customer" | "reconciliation";
const PAYMENT_ENTRIES_TAB_CACHE_KEY = "klik_pos_payment_entries_active_tab";

const getCachedPaymentTab = (): PaymentTab => {
  if (typeof window === "undefined") return "invoice";
  const cached = window.localStorage.getItem(PAYMENT_ENTRIES_TAB_CACHE_KEY);
  return cached === "customer" || cached === "reconciliation" || cached === "invoice" ? cached : "invoice";
};

const buildModalCustomer = (id: string, name: string): Customer =>
  ({
    id,
    name,
    email: "",
    phone: "",
    address: { street: "", city: "", state: "", zipCode: "", country: "" },
    loyaltyPoints: 0,
    totalSpent: 0,
    totalOrders: 0,
    preferredPaymentMethod: "Cash",
    tags: [],
    status: "active",
    type: "individual",
    createdAt: new Date().toISOString(),
  }) as Customer;

export default function PaymentEntriesPage() {
  const { currencySymbol } = usePOSProfileStore();
  const [activeTab, setActiveTab] = useState<PaymentTab>(getCachedPaymentTab);
  const [invoiceSearch, setInvoiceSearch] = useState("");
  const [customerSearch, setCustomerSearch] = useState("");
  const [invoices, setInvoices] = useState<OutstandingSalesInvoice[]>([]);
  const [invoiceTotal, setInvoiceTotal] = useState(0);
  const [isLoadingInvoices, setIsLoadingInvoices] = useState(true);
  const [invoiceError, setInvoiceError] = useState<string | null>(null);
  const [selectedInvoice, setSelectedInvoice] = useState<OutstandingSalesInvoice | null>(null);
  const [receivables, setReceivables] = useState<CustomerReceivable[]>([]);
  const [receivablesCurrency, setReceivablesCurrency] = useState("");
  const [isLoadingReceivables, setIsLoadingReceivables] = useState(false);
  const [receivablesError, setReceivablesError] = useState<string | null>(null);
  const [receivableTarget, setReceivableTarget] = useState<{
    customer: Customer;
    salesInvoiceName?: string;
    outstandingAmount?: number;
    defaultAmount: number;
    allocationTargets?: ReceivableInvoice[];
  } | null>(null);
  const [reconcileSearch, setReconcileSearch] = useState("");
  const [reconcileInvoices, setReconcileInvoices] = useState<OutstandingSalesInvoice[]>([]);
  const [credits, setCredits] = useState<UnallocatedCustomerPaymentEntry[]>([]);
  const [isLoadingReconciliation, setIsLoadingReconciliation] = useState(true);
  const [reconciliationError, setReconciliationError] = useState<string | null>(null);
  const [selectedCredit, setSelectedCredit] = useState<UnallocatedCustomerPaymentEntry | null>(null);
  const [selectedReconcileInvoice, setSelectedReconcileInvoice] = useState<OutstandingSalesInvoice | null>(null);
  const [reconcileAmount, setReconcileAmount] = useState("");
  const [isReconciling, setIsReconciling] = useState(false);

  const handleTabChange = (tab: PaymentTab) => {
    setActiveTab(tab);
    window.localStorage.setItem(PAYMENT_ENTRIES_TAB_CACHE_KEY, tab);
  };

  useEffect(() => {
    let isCurrent = true;
    const timer = window.setTimeout(async () => {
      setIsLoadingInvoices(true);
      setInvoiceError(null);
      try {
        const response = await getOutstandingSalesInvoices(invoiceSearch);
        if (!isCurrent) return;
        setInvoices(response.data || []);
        setInvoiceTotal(response.total_count || 0);
      } catch (err) {
        if (!isCurrent) return;
        setInvoiceError(err instanceof Error ? err.message : "Failed to fetch outstanding invoices");
        setInvoices([]);
        setInvoiceTotal(0);
      } finally {
        if (isCurrent) setIsLoadingInvoices(false);
      }
    }, invoiceSearch ? 300 : 0);

    return () => {
      isCurrent = false;
      window.clearTimeout(timer);
    };
  }, [invoiceSearch]);

  useEffect(() => {
    let isCurrent = true;
    const timer = window.setTimeout(async () => {
      setIsLoadingReconciliation(true);
      setReconciliationError(null);
      try {
        const [invoiceResponse, creditResponse] = await Promise.all([
          getOutstandingSalesInvoices(reconcileSearch),
          getUnallocatedCustomerPaymentEntries(reconcileSearch),
        ]);
        if (!isCurrent) return;
        setReconcileInvoices(invoiceResponse.data || []);
        setCredits(creditResponse.data || []);
      } catch (err) {
        if (!isCurrent) return;
        setReconciliationError(err instanceof Error ? err.message : "Failed to fetch reconciliation data");
        setReconcileInvoices([]);
        setCredits([]);
      } finally {
        if (isCurrent) setIsLoadingReconciliation(false);
      }
    }, reconcileSearch ? 300 : 0);

    return () => {
      isCurrent = false;
      window.clearTimeout(timer);
    };
  }, [reconcileSearch]);

  const { sortedData: sortedInvoices, sortKey: invoiceSortKey, sortDirection: invoiceSortDirection, toggleSort: toggleInvoiceSort } = useTableSort(
    invoices,
    {
      name: (invoice) => invoice.name,
      customer: (invoice) => invoice.customer_name || invoice.customer,
      date: (invoice) => toSortableTimestamp(invoice.posting_date, invoice.posting_time),
      outstanding: (invoice) => invoice.outstanding_amount,
      status: (invoice) => invoice.status,
    }
  );

  const invoiceCustomer = useMemo(() => {
    if (!selectedInvoice) return null;
    return buildModalCustomer(
      selectedInvoice.customer,
      selectedInvoice.customer_name || selectedInvoice.customer
    );
  }, [selectedInvoice]);

  const loadReceivables = useCallback(async () => {
    setIsLoadingReceivables(true);
    setReceivablesError(null);
    try {
      const response = await getCustomerReceivables();
      setReceivables(response.data || []);
      setReceivablesCurrency(response.currency || "");
    } catch (err) {
      setReceivablesError(err instanceof Error ? err.message : "Failed to fetch customer receivables");
      setReceivables([]);
    } finally {
      setIsLoadingReceivables(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab !== "customer") return;
    loadReceivables();
  }, [activeTab, loadReceivables]);

  const filteredReceivables = useMemo(() => {
    const term = customerSearch.trim().toLowerCase();
    if (!term) return receivables;
    return receivables.filter(
      (receivable) =>
        receivable.customer.toLowerCase().includes(term) ||
        receivable.customer_name.toLowerCase().includes(term) ||
        receivable.invoices.some((invoice) => invoice.name.toLowerCase().includes(term))
    );
  }, [receivables, customerSearch]);

  const closePaymentModal = () => {
    setSelectedInvoice(null);
  };

  const handlePaymentCreated = () => {
    getOutstandingSalesInvoices(invoiceSearch)
      .then((response) => {
        setInvoices(response.data || []);
        setInvoiceTotal(response.total_count || 0);
      })
      .catch(() => {
        // The toast from the modal already confirms creation; avoid noisy reload errors.
      });
  };

  useEffect(() => {
    if (!selectedCredit || !selectedReconcileInvoice) {
      setReconcileAmount("");
      return;
    }
    const amount = Math.min(
      Number(selectedCredit.unallocated_amount || 0),
      Number(selectedReconcileInvoice.outstanding_amount || 0)
    );
    setReconcileAmount(amount > 0 ? String(amount) : "");
  }, [selectedCredit, selectedReconcileInvoice]);

  const refreshReconciliation = async () => {
    const [invoiceResponse, creditResponse] = await Promise.all([
      getOutstandingSalesInvoices(reconcileSearch),
      getUnallocatedCustomerPaymentEntries(reconcileSearch),
    ]);
    setReconcileInvoices(invoiceResponse.data || []);
    setCredits(creditResponse.data || []);
  };

  const handleReconcile = async () => {
    if (!selectedCredit || !selectedReconcileInvoice) return;
    const amount = Number(reconcileAmount);
    if (amount <= 0) {
      toast.error("Enter an amount greater than zero.");
      return;
    }
    if (selectedCredit.customer !== selectedReconcileInvoice.customer) {
      toast.error("Select a payment entry and invoice for the same customer.");
      return;
    }

    setIsReconciling(true);
    try {
      await reconcilePaymentEntryWithInvoice(selectedCredit.name, selectedReconcileInvoice.name, amount);
      toast.success("Payment reconciled successfully.");
      setSelectedCredit(null);
      setSelectedReconcileInvoice(null);
      setReconcileAmount("");
      await refreshReconciliation();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to reconcile payment");
    } finally {
      setIsReconciling(false);
    }
  };

  const canReconcile = Boolean(
    selectedCredit &&
      selectedReconcileInvoice &&
      selectedCredit.customer === selectedReconcileInvoice.customer &&
      Number(reconcileAmount) > 0 &&
      Number(reconcileAmount) <= Number(selectedCredit?.unallocated_amount || 0) &&
      Number(reconcileAmount) <= Number(selectedReconcileInvoice?.outstanding_amount || 0) &&
      !isReconciling
  );

  return (
    <div className="min-h-screen bg-gray-50 pb-20 dark:bg-gray-900 lg:ml-20 lg:pb-2">
      <div className="sticky top-0 z-40 border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div className="px-4 py-4 sm:px-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Payment Entries</h1>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                {invoiceTotal} outstanding invoice{invoiceTotal === 1 ? "" : "s"}
              </p>
            </div>
            <div className="inline-flex rounded-lg border border-gray-200 bg-gray-100 p-1 dark:border-gray-700 dark:bg-gray-800">
              <button
                type="button"
                onClick={() => handleTabChange("invoice")}
                className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium ${
                  activeTab === "invoice"
                    ? "bg-white text-beveren-700 shadow-sm dark:bg-gray-900 dark:text-beveren-300"
                    : "text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
                }`}
              >
                <FileText size={16} />
                <span>By Invoice</span>
              </button>
              <button
                type="button"
                onClick={() => handleTabChange("customer")}
                className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium ${
                  activeTab === "customer"
                    ? "bg-white text-beveren-700 shadow-sm dark:bg-gray-900 dark:text-beveren-300"
                    : "text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
                }`}
              >
                <User size={16} />
                <span>By Customer</span>
              </button>
              <button
                type="button"
                onClick={() => handleTabChange("reconciliation")}
                className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium ${
                  activeTab === "reconciliation"
                    ? "bg-white text-beveren-700 shadow-sm dark:bg-gray-900 dark:text-beveren-300"
                    : "text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
                }`}
              >
                <ArrowRightLeft size={16} />
                <span>Reconcile</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <main className="px-4 py-4 sm:px-6">
        {activeTab === "invoice" ? (
          <section className="space-y-4">
            <div className="relative max-w-xl">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
              <input
                type="text"
                value={invoiceSearch}
                onChange={(event) => setInvoiceSearch(event.target.value)}
                placeholder="Search invoice or customer"
                className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-10 pr-3 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
              />
            </div>

            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
              <div className="overflow-x-auto">
                <table className="min-w-full">
                  <thead className="bg-gray-50 dark:bg-gray-700">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                        <SortableHeaderButton label="Invoice" sortKey="name" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                        <SortableHeaderButton label="Customer" sortKey="customer" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider whitespace-nowrap">
                        <SortableHeaderButton label="Date" sortKey="date" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                        <SortableHeaderButton label="Status" sortKey="status" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                      </th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                        <span className="inline-flex w-full justify-end">
                          <SortableHeaderButton label="Outstanding" sortKey="outstanding" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                        </span>
                      </th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                        Action
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 dark:divide-gray-600">
                    {isLoadingInvoices ? (
                      <tr>
                        <td colSpan={6} className="px-6 py-10 text-center text-gray-500 dark:text-gray-400">
                          <span className="inline-flex items-center gap-2">
                            <Loader2 size={18} className="animate-spin" />
                            Loading invoices...
                          </span>
                        </td>
                      </tr>
                    ) : invoiceError ? (
                      <tr>
                        <td colSpan={6} className="px-6 py-10 text-center text-red-600 dark:text-red-400">
                          {invoiceError}
                        </td>
                      </tr>
                    ) : invoices.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-6 py-10 text-center text-gray-500 dark:text-gray-400">
                          No outstanding invoices found.
                        </td>
                      </tr>
                    ) : (
                      sortedInvoices.map((invoice) => (
                        <tr key={invoice.name} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                          <td className="px-6 py-4 whitespace-nowrap">
                            <div className="text-sm font-medium text-gray-900 dark:text-white">{invoice.name}</div>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-white">
                            {invoice.customer_name || invoice.customer}
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap">
                            <div className="text-sm text-gray-900 dark:text-white">{formatDateTime(invoice.posting_date, invoice.posting_time)}</div>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap">
                            <span className={getStatusBadge(invoice.status)}>{invoice.status}</span>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium text-gray-900 dark:text-white">
                            {formatCurrencyWithSymbol(invoice.outstanding_amount, invoice.currency)}
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                            <button
                              type="button"
                              onClick={() => setSelectedInvoice(invoice)}
                              className="inline-flex items-center gap-2 rounded-lg bg-beveren-600 px-3 py-2 text-sm font-medium text-white hover:bg-beveren-700"
                            >
                              <Banknote size={16} />
                              <span>Receive</span>
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        ) : activeTab === "customer" ? (
          <section className="space-y-4">
            <div className="relative max-w-xl">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
              <input
                type="text"
                value={customerSearch}
                onChange={(event) => setCustomerSearch(event.target.value)}
                placeholder="Search customer or invoice"
                className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-10 pr-3 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
              />
            </div>

            <CustomerReceivablesTable
              receivables={filteredReceivables}
              // Company.default_currency can be NULL; fall back to the POS profile's
              // currency symbol so amounts never render without a currency marker.
              currency={receivablesCurrency || currencySymbol}
              isLoading={isLoadingReceivables}
              error={receivablesError}
              onReceiveCustomer={(receivable) =>
                setReceivableTarget({
                  customer: buildModalCustomer(receivable.customer, receivable.customer_name),
                  defaultAmount: Math.max(0, receivable.outstanding),
                  allocationTargets: receivable.invoices,
                })
              }
              onReceiveInvoice={(receivable, invoice) =>
                setReceivableTarget({
                  customer: buildModalCustomer(receivable.customer, receivable.customer_name),
                  salesInvoiceName: invoice.name,
                  outstandingAmount: invoice.outstanding,
                  defaultAmount: invoice.outstanding,
                })
              }
            />
          </section>
        ) : (
          <section className="space-y-4">
            <div className="relative max-w-xl">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
              <input
                type="text"
                value={reconcileSearch}
                onChange={(event) => setReconcileSearch(event.target.value)}
                placeholder="Search customer, invoice, or payment entry"
                className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-10 pr-3 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
              />
            </div>

            {reconciliationError && (
              <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
                {reconciliationError}
              </div>
            )}

            <div className="grid gap-3 xl:grid-cols-2">
              <div className="rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
                <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-700">
                  <h2 className="flex items-center gap-2 font-semibold text-gray-900 dark:text-white">
                    <CreditCard size={18} />
                    <span>Credit / Payment Entries</span>
                  </h2>
                  <span className="text-sm text-gray-500 dark:text-gray-400">{credits.length}</span>
                </div>
                <div className="max-h-[560px] overflow-y-auto divide-y divide-gray-200 dark:divide-gray-700">
                  {isLoadingReconciliation ? (
                    <div className="p-3 text-gray-500 dark:text-gray-400">
                      <span className="inline-flex items-center gap-2">
                        <Loader2 size={18} className="animate-spin" />
                        Loading credits...
                      </span>
                    </div>
                  ) : credits.length === 0 ? (
                    <div className="p-3 text-gray-500 dark:text-gray-400">No unallocated payment entries found.</div>
                  ) : (
                    credits.map((credit) => {
                      const isSelected = selectedCredit?.name === credit.name;
                      return (
                        <button
                          key={credit.name}
                          type="button"
                          onClick={() => setSelectedCredit(credit)}
                          className={`w-full px-4 py-3 text-left transition-colors ${
                            isSelected
                              ? "bg-beveren-50 dark:bg-beveren-950/30"
                              : "hover:bg-gray-50 dark:hover:bg-gray-700/50"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="truncate font-medium text-gray-900 dark:text-white">{credit.name}</div>
                              <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                                {credit.customer_name || credit.customer} • {credit.mode_of_payment || "Payment Entry"}
                              </div>
                              <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{credit.posting_date}</div>
                            </div>
                            <div className="text-right">
                              <div className="font-semibold text-green-700 dark:text-green-300">
                                {formatCurrencyWithSymbol(credit.unallocated_amount, credit.currency)}
                              </div>
                              <div className="text-xs text-gray-500 dark:text-gray-400">unallocated</div>
                            </div>
                          </div>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
                <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-700">
                  <h2 className="flex items-center gap-2 font-semibold text-gray-900 dark:text-white">
                    <FileText size={18} />
                    <span>Outstanding Invoices</span>
                  </h2>
                  <span className="text-sm text-gray-500 dark:text-gray-400">{reconcileInvoices.length}</span>
                </div>
                <div className="max-h-[560px] overflow-y-auto divide-y divide-gray-200 dark:divide-gray-700">
                  {isLoadingReconciliation ? (
                    <div className="p-3 text-gray-500 dark:text-gray-400">
                      <span className="inline-flex items-center gap-2">
                        <Loader2 size={18} className="animate-spin" />
                        Loading invoices...
                      </span>
                    </div>
                  ) : reconcileInvoices.length === 0 ? (
                    <div className="p-3 text-gray-500 dark:text-gray-400">No outstanding invoices found.</div>
                  ) : (
                    reconcileInvoices.map((invoice) => {
                      const isSelected = selectedReconcileInvoice?.name === invoice.name;
                      const incompatible = selectedCredit && selectedCredit.customer !== invoice.customer;
                      return (
                        <button
                          key={invoice.name}
                          type="button"
                          onClick={() => setSelectedReconcileInvoice(invoice)}
                          className={`w-full px-4 py-3 text-left transition-colors ${
                            isSelected
                              ? "bg-beveren-50 dark:bg-beveren-950/30"
                              : "hover:bg-gray-50 dark:hover:bg-gray-700/50"
                          } ${incompatible ? "opacity-50" : ""}`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="truncate font-medium text-gray-900 dark:text-white">{invoice.name}</div>
                              <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                                {invoice.customer_name || invoice.customer}
                              </div>
                              <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{invoice.posting_date}</div>
                            </div>
                            <div className="text-right">
                              <div className="font-semibold text-red-700 dark:text-red-300">
                                {formatCurrencyWithSymbol(invoice.outstanding_amount, invoice.currency)}
                              </div>
                              <div className="text-xs text-gray-500 dark:text-gray-400">outstanding</div>
                            </div>
                          </div>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            </div>

            <div className="sticky bottom-20 rounded-lg border border-gray-200 bg-white p-3 shadow-lg dark:border-gray-700 dark:bg-gray-800 lg:bottom-4">
              <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
                <div>
                  <div className="text-xs font-medium uppercase text-gray-500 dark:text-gray-400">Selected Credit</div>
                  <div className="mt-1 text-sm font-medium text-gray-900 dark:text-white">
                    {selectedCredit
                      ? `${selectedCredit.name} • ${formatCurrencyWithSymbol(selectedCredit.unallocated_amount, selectedCredit.currency)}`
                      : "No payment entry selected"}
                  </div>
                </div>
                <div>
                  <div className="text-xs font-medium uppercase text-gray-500 dark:text-gray-400">Selected Invoice</div>
                  <div className="mt-1 text-sm font-medium text-gray-900 dark:text-white">
                    {selectedReconcileInvoice
                      ? `${selectedReconcileInvoice.name} • ${formatCurrencyWithSymbol(
                          selectedReconcileInvoice.outstanding_amount,
                          selectedReconcileInvoice.currency
                        )}`
                      : "No invoice selected"}
                  </div>
                </div>
                <div className="flex items-end gap-3">
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                      Allocate
                    </label>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={reconcileAmount}
                      onChange={(event) => setReconcileAmount(event.target.value)}
                      className="w-36 rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-900 dark:text-white"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={handleReconcile}
                    disabled={!canReconcile}
                    className="inline-flex items-center gap-2 rounded-lg bg-beveren-600 px-4 py-2 text-sm font-medium text-white hover:bg-beveren-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                  >
                    {isReconciling ? <Loader2 size={16} className="animate-spin" /> : <ArrowRightLeft size={16} />}
                    <span>Reconcile</span>
                  </button>
                </div>
              </div>
            </div>
          </section>
        )}
      </main>

      {selectedInvoice && invoiceCustomer && (
        <CustomerPaymentEntryModal
          customer={invoiceCustomer}
          salesInvoiceName={selectedInvoice.name}
          outstandingAmount={selectedInvoice.outstanding_amount}
          defaultAmount={selectedInvoice.outstanding_amount}
          invoiceCurrency={selectedInvoice.currency}
          onClose={closePaymentModal}
          onCreated={handlePaymentCreated}
        />
      )}

      {receivableTarget && (
        <CustomerPaymentEntryModal
          customer={receivableTarget.customer}
          salesInvoiceName={receivableTarget.salesInvoiceName}
          outstandingAmount={receivableTarget.outstandingAmount}
          defaultAmount={receivableTarget.defaultAmount}
          invoiceCurrency={receivablesCurrency || currencySymbol}
          allocationTargets={receivableTarget.allocationTargets}
          onClose={() => setReceivableTarget(null)}
          onCreated={() => {
            setReceivableTarget(null);
            loadReceivables();
            // Also refresh the By Invoice tab's data: a payment made here can settle
            // an invoice shown there, and that table only refetches on invoiceSearch
            // changes, not on tab switches. Without this, the stale outstanding figure
            // leads to a "no outstanding amount" error if the user tries to Receive again.
            handlePaymentCreated();
          }}
        />
      )}

      <div className="lg:hidden">
        <BottomNavigation />
      </div>
    </div>
  );
}

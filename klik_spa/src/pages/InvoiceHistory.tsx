import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  FileText,
  Clock,
  CheckCircle,
  XCircle,
  AlertTriangle,
  FilePlus,
  RefreshCw,
  Download,
  Search,
  Grid3X3,
  List,
  Eye,
  Edit,
  Users,
  RotateCcw,
  Check,
  FileMinus,
  X,
  MessageCirclePlus,
  ShoppingCart,
  FileSignature,
  Copy,
} from "lucide-react";

import InvoiceViewModal from "../components/InvoiceViewModal";
import BottomNavigation from "../components/BottomNavigation";
import MultiInvoiceReturn from "../components/MultiInvoiceReturn";
import { MULTI_INVOICE_RETURN_ENABLED } from "../utils/featureFlags";
import SingleInvoiceReturn from "../components/SingleInvoiceReturn";
import SalespersonAuthModal from "../components/dialog/SalespersonAuthModal";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { formatCurrencyWithSymbol } from "../utils/currency";
import type { SalesInvoice } from "../../types";
import { useSalesInvoices } from "../hooks/useSalesInvoices";
import { useCustomers } from "../hooks/useCustomers";
import { useUserInfo } from "../hooks/useUserInfo";
import { usePOSProfileStore } from "../stores/posProfileStore";
import { quotesAllowed } from "../utils/quotesAllowed";
import { useSalespersonStore } from "../stores/salespersonStore";
import { toast } from "react-toastify";
import { extractErrorFromException } from "../utils/errorExtraction";
import { createSalesReturn, retryQueuedInvoice } from "../services/salesInvoice";
import { useAllPaymentModes } from "../hooks/usePaymentModes";
import PaymentDialog from "../components/dialog/PaymentDialog";
import { addDraftInvoiceToCart } from "../utils/draftInvoiceToCart";
import { addHeldOrderToCart, fetchHeldOrder, mergeHeldOrderIntoCart, mergeRefusal } from "../utils/heldOrderToCart";
import { heldItemsToCart } from "../utils/heldOrderPayload";
import { canCombine } from "../utils/mergeHeldOrder";
import { ChoiceDialog } from "../components/ui/ChoiceDialog";
import { addQuoteCopyToCart, addQuoteToCart } from "../utils/quoteToCart";
import { quoteStatusLabel } from "../utils/quoteStatus";
import { getQuotes } from "../services/quotation";
import QuoteSavedDialog from "../components/dialog/QuoteSavedDialog";
import DocShareButtons from "../components/DocShareButtons";
import {
  applyHeldOrderAction,
  getHeldOrders,
} from "../services/salesOrder";
import { getOriginalHeldOrderId, loadCachedItemsToCart } from "../utils/draftInvoiceCache";
import { heldStatusLabel, PRICE_APPROVED, WITHDRAW_APPROVAL } from "../utils/priceApproval";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { handlePrintInvoice } from "../utils/printHandler";
import { useCartStore } from "../stores/cartStore";
import { formatDateTime, toSortableTimestamp } from "../utils/time";
import { inDateRange, storedDate } from "../utils/dateRange";
import { DateRangeFilter } from "../components/DateRangeFilter";
import { exportInvoicesToCSV, getExportFilename, type ExportableInvoice } from "../utils/exportUtils";
import { useTableSort } from "../hooks/useTableSort";
import SortableHeaderButton from "../components/SortableHeaderButton";
import { walkinSubline } from "../utils/walkinLabel";
import { matchesPaymentFilter } from "../utils/paymentFilter";
import { countFailed, matchesInvoiceTab, normalizeInvoiceTab, rowsForTab, sortFailedFirst, tabPagesInvoices } from "../utils/invoiceHistoryTabs";
// import InvoiceViewPage from "./InvoiceViewPage";

interface HeldOrderExtras {
  isHeldOrder?: boolean;
  approvalState?: string | null;
  priceBreach?: number;
  /** Approve / Reject, when the caller may take them on this pending order. */
  approvalActions?: string[];
  /** Kept for an M-Pesa push sent from it: finishing it picks the payment up. */
  mpesaOrder?: boolean;
  /** What the order would come to if checked out now, on a till that records Loss of Sale:
   * the order holds what was asked for, out-of-stock lines included. */
  inStockTotal?: number;
  /** A saved Quotation (Quotes tab): print, WhatsApp, load to cart - nothing of an invoice's. */
  isQuote?: boolean;
  /** A quote's state (get_quotes) and, once converted, the invoice it became. */
  quoteState?: "open" | "converted" | "closed";
  quoteInvoice?: string | null;
}

/** "Sh 450 in stock" under a held order's total, when not all of it is. */
const inStockLine = (invoice: SalesInvoice & HeldOrderExtras) =>
  invoice.inStockTotal !== undefined && invoice.inStockTotal !== invoice.totalAmount ? (
    <div className="text-xs text-amber-700 dark:text-amber-400">
      {formatCurrencyWithSymbol(invoice.inStockTotal, invoice.currency)} in stock
    </div>
  ) : null;

const INVOICE_HISTORY_VIEW_MODE_KEY = "invoice-history-view-mode";
const INVOICE_HISTORY_FILTERS_KEY = "invoice-history-filters";

interface InvoiceHistoryFiltersState {
  activeTab: string;
  searchTerm: string;
  fromDate: string;
  toDate: string;
  paymentFilter: string;
  cashierFilter: string;
}

const DEFAULT_INVOICE_HISTORY_FILTERS: InvoiceHistoryFiltersState = {
  activeTab: "all",
  searchTerm: "",
  fromDate: "",
  toDate: "",
  paymentFilter: "all",
  cashierFilter: "all",
};

const getInitialInvoiceHistoryFilters = (): InvoiceHistoryFiltersState => {
  if (typeof window === "undefined") {
    return DEFAULT_INVOICE_HISTORY_FILTERS;
  }

  try {
    const raw = window.localStorage.getItem(INVOICE_HISTORY_FILTERS_KEY);
    if (!raw) {
      return DEFAULT_INVOICE_HISTORY_FILTERS;
    }

    // Older saves carry a `dateFilter` preset instead of From/To: it is never read, and the
    // next save drops it.
    const parsed = JSON.parse(raw) as Partial<InvoiceHistoryFiltersState>;
    return {
      ...DEFAULT_INVOICE_HISTORY_FILTERS,
      ...parsed,
      fromDate: storedDate(parsed.fromDate),
      toDate: storedDate(parsed.toDate),
      // The Held tab used to be called Draft; a tab remembered from then should still open it.
      activeTab: parsed.activeTab === "Draft" ? "Held" : normalizeInvoiceTab(parsed.activeTab || DEFAULT_INVOICE_HISTORY_FILTERS.activeTab),
    };
  } catch {
    return DEFAULT_INVOICE_HISTORY_FILTERS;
  }
};

/**
 * A tab named in the URL, as the Sales Dashboard's exception rows name it.
 *
 * The link has to win over the remembered filters for that visit, or tapping "2 sales failed
 * to submit" lands the reader on whatever tab they were last looking at. It is deliberately
 * not written back to storage: the reader followed a link, they did not change their mind
 * about where Invoice History should open next time.
 */
const URL_TAB_ALIASES: Record<string, string> = {
  queue_failed: "queued",
  queued: "queued",
  draft: "Held",
  held: "Held",
};

const getUrlTab = (search: string): string | null => {
  try {
    const requested = new URLSearchParams(search).get("tab");
    return requested ? (URL_TAB_ALIASES[requested] ?? null) : null;
  } catch {
    return null;
  }
};

const getInitialViewMode = (): "cards" | "list" => {
  if (typeof window === "undefined") {
    return "list";
  }

  return window.localStorage.getItem(INVOICE_HISTORY_VIEW_MODE_KEY) === "cards"
    ? "cards"
    : "list";
};

export default function InvoiceHistoryPage() {
  const initialFilters = getInitialInvoiceHistoryFilters();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const urlTab = getUrlTab(searchParams.toString());
  const isMobile = useMediaQuery("(max-width: 1024px)");
  const [activeTab, setActiveTab] = useState(urlTab ?? initialFilters.activeTab);
  // One write is skipped so a tab arrived at by link does not become the remembered one.
  const skipFilterPersist = useRef(Boolean(urlTab));
  const [searchTerm, setSearchTerm] = useState(initialFilters.searchTerm);
  const [fromDate, setFromDate] = useState(initialFilters.fromDate);
  const [toDate, setToDate] = useState(initialFilters.toDate);
  // The Held tab's Status filter; its choices are the statuses the held orders carry.
  const [heldStatusFilter, setHeldStatusFilter] = useState("all");
  const [paymentFilter, setPaymentFilter] = useState(initialFilters.paymentFilter);
  const [cashierFilter, setCashierFilter] = useState(initialFilters.cashierFilter);
  const [viewMode, setViewMode] = useState<"cards" | "list">(getInitialViewMode);
  const [selectedInvoice] = useState<SalesInvoice | null>(null);
  const [showInvoiceModal, setShowInvoiceModal] = useState(false);

  // Multi-Invoice Return states
  const [showCustomerSelection, setShowCustomerSelection] = useState(false);
  const [showMultiReturn, setShowMultiReturn] = useState(false);
  const [selectedCustomer, setSelectedCustomer] = useState("");
  const [customerSearchQuery, setCustomerSearchQuery] = useState("");


  // Single Invoice Return states
  const [showSingleReturn, setShowSingleReturn] = useState(false);
  const [selectedInvoiceForReturn, setSelectedInvoiceForReturn] = useState<SalesInvoice | null>(null);
  const [showDraftPaymentDialog, setShowDraftPaymentDialog] = useState(false);
  const [showSalespersonAuthModal, setShowSalespersonAuthModal] = useState(false);
  const pendingSalespersonActionRef = useRef<null | (() => void)>(null);

  // Skip opening entry filter for Invoice History - show all invoices for cashier regardless of opening entry
  // Pass cashier filter to API so it filters on server side (more efficient)
  const { invoices, isLoading, isLoadingMore, error, hasMore, totalLoaded, totalCount, loadMore, refetch } = useSalesInvoices(searchTerm, true, cashierFilter, false, "history", fromDate, toDate);
  const pagesInvoices = tabPagesInvoices(activeTab);

  // Held draft Sales Orders (custom_is_klik_held=1) — surfaced under the Held tab.
  // Mapped to the SalesInvoice shape with `isHeldOrder` so the row + action handlers
  // can route through the Sales Order flow instead of the (now empty) draft-SI flow.
  const [heldOrders, setHeldOrders] = useState<(SalesInvoice & HeldOrderExtras)[]>([]);
  const [withdrawTarget, setWithdrawTarget] = useState<string | null>(null);
  // Edit on a held order while the cart holds something else: replace or merge, then (if the
  // same items are in both) separate lines or combined quantities.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [mergeAsk, setMergeAsk] = useState<{ id: string; data: any; step: "how" | "combine" } | null>(null);
  const [isWithdrawingApproval, setIsWithdrawingApproval] = useState(false);
  const refetchHeldOrders = useCallback(async () => {
    try {
      const res = await getHeldOrders({ skipOpeningEntryFilter: true, search: searchTerm, limit: 200 });
      if (!res?.success) {
        setHeldOrders([]);
        return;
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const mapped = (res.data || []).map((o: any) => ({
        id: o.name,
        name: o.name,
        date: o.transaction_date || new Date().toISOString().split("T")[0],
        time: "",
        // Full name, not the email: the cashier filter compares against full names, so an
        // email here made every held order fail it and the Held tab came up empty.
        cashier: o.cashier_name || o.cashier || o.owner || "",
        cashierId: o.owner || "",
        customer: o.customer_name || o.customer || "",
        customerId: o.customer || "",
        walkinName: o.custom_walkin_customer_name || "",
        items: o.items || [],
        subtotal: Number(o.grand_total) || 0,
        giftCardDiscount: 0,
        giftCardCode: "",
        taxAmount: 0,
        totalAmount: Number(o.grand_total) || 0,
        paymentMethod: "-",
        payment_methods: [],
        amountPaid: 0,
        changeGiven: 0,
        status: heldStatusLabel(o.approval_state, Number(o.price_breach) || 0),
        refundAmount: 0,
        currency: o.currency || "USD",
        notes: "",
        posProfile: o.custom_pos_profile || "",
        custom_pos_opening_entry: o.custom_pos_opening_entry || "",
        canReturn: false,
        isHeldOrder: true,
        approvalState: o.approval_state ?? null,
        priceBreach: Number(o.price_breach) || 0,
        approvalActions: Array.isArray(o.approval_actions) ? o.approval_actions : [],
        mpesaOrder: Boolean(o.mpesa_order),
        inStockTotal: o.in_stock_total === undefined || o.in_stock_total === null ? undefined : Number(o.in_stock_total),
      })) as unknown as (SalesInvoice & HeldOrderExtras)[];
      setHeldOrders(mapped);
    } catch {
      setHeldOrders([]);
    }
  }, [searchTerm]);

  // Again on every tab switch (the Held count shows on all of them): a manager's approval
  // elsewhere then turns the badge green without a page reload.
  useEffect(() => {
    void refetchHeldOrders();
  }, [refetchHeldOrders, activeTab]);

  // Open quotes of this company, from any till; mapped to the row shape like held orders.
  const [quotes, setQuotes] = useState<(SalesInvoice & HeldOrderExtras)[]>([]);
  const [quoteToShare, setQuoteToShare] = useState<{ name: string; customer: string } | null>(null);
  // Which quotes the Quotes tab lists: open (loadable for sale), converted, closed, or all.
  const [quoteState, setQuoteState] = useState<"open" | "converted" | "closed" | "all">("open");
  useEffect(() => {
    let live = true;
    void getQuotes({ search: searchTerm, limit: 200, state: quoteState }).then((res) => {
      if (!live) return;
      // Refused (no Quotation read, say): say so, rather than show an empty tab.
      if (!res.success && activeTab === "Quotes") toast.error(res.message || "Failed to load quotes");
      setQuotes(res.success ? (res.data || []).map((q) => ({
        id: q.name,
        name: q.name,
        date: q.transaction_date,
        time: "",
        cashier: "",
        customer: q.customer_name || q.customer,
        customerId: q.customer,
        walkinName: q.walkin_name || "",
        items: [],
        subtotal: Number(q.grand_total) || 0,
        totalAmount: Number(q.grand_total) || 0,
        giftCardDiscount: 0,
        paymentMethod: "-",
        status: quoteStatusLabel(q),
        currency: q.currency,
        canReturn: false,
        isQuote: true,
        quoteState: q.state,
        quoteInvoice: q.invoice,
      })) as unknown as (SalesInvoice & HeldOrderExtras)[] : []);
    });
    return () => { live = false; };
  }, [searchTerm, activeTab, quoteState]);

  const handleLoadQuote = async (quoteId: string) => {
    try {
      if (await addQuoteToCart(quoteId)) navigate("/pos");
    } catch (error) {
      console.error("Error loading quote:", error);
      toast.error(error instanceof Error ? error.message : "Failed to load the quote");
    }
  };

  const quoteExtras = (row: SalesInvoice) => row as SalesInvoice & HeldOrderExtras;

  const handleCopyQuote = async (quoteId: string) => {
    try {
      if (await addQuoteCopyToCart(quoteId)) navigate("/pos");
    } catch (error) {
      console.error("Error copying quote:", error);
      toast.error(error instanceof Error ? error.message : "Failed to copy the quote");
    }
  };

  const renderQuoteButtons = (invoice: SalesInvoice, size: "sm" | "md") => {
    const icon = size === "sm" ? "w-4 h-4" : "w-5 h-5";
    const button = (size === "sm" ? "p-1" : "p-2.5") + " rounded text-gray-600 hover:text-beveren-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:text-white dark:hover:bg-gray-700";
    const quoteId = invoice.id || invoice.name;
    return (
      <>
        <DocShareButtons doctype="Quotation" name={quoteId} size={size} posDetails={posDetails} />
        <button type="button" title="Send quote (WhatsApp or email)" aria-label="Send quote" className={button}
          onClick={() => setQuoteToShare({ name: quoteId, customer: invoice.customer })}>
          <MessageCirclePlus className={icon} />
        </button>
        {quoteExtras(invoice).quoteInvoice ? (
          <button type="button" onClick={() => navigate(`/invoice/${quoteExtras(invoice).quoteInvoice}`)}
            title="Open the invoice this quote became"
            className="text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white text-xs underline">
            {quoteExtras(invoice).quoteInvoice}
          </button>
        ) : null}
        {quoteExtras(invoice).quoteState === "open" || !quoteExtras(invoice).quoteState ? (
          <button type="button" onClick={() => void handleLoadQuote(quoteId)}
            className="text-blue-600 hover:text-blue-900 dark:text-blue-400 dark:hover:text-blue-300 flex items-center space-x-1">
            <ShoppingCart className={icon} />
            <span>Load to cart</span>
          </button>
        ) : (
          <button type="button" onClick={() => void handleCopyQuote(quoteId)}
            title="Its lines and customer, as a new sale at today's prices"
            className="text-blue-600 hover:text-blue-900 dark:text-blue-400 dark:hover:text-blue-300 flex items-center space-x-1">
            <Copy className={icon} />
            <span>Copy to cart</span>
          </button>
        )}
      </>
    );
  };

  // POS Profile "Block Printing Held Orders": the server refuses these anyway (Sales Order
  // has_permission); shown, but disabled.
  const heldOrderPrintBlocked = () =>
    [1, "1", true].includes(posDetails?.custom_block_held_order_print as 1 | "1" | true);
  const renderHeldOrderShareButtons = (orderId: string, size: "sm" | "md") => (
    <DocShareButtons doctype="Sales Order" name={orderId} size={size} posDetails={posDetails}
      blockedTitle={heldOrderPrintBlocked() ? "Printing held orders is blocked on this till" : undefined} />
  );


  const { modes } = useAllPaymentModes();
  const { cartItems, selectedCustomer: cartCustomer } = useCartStore();
  const { customers } = useCustomers();
  const { posDetails } = usePOSProfileStore();
  const { activeSalesperson, ensureInitialized } = useSalespersonStore();
  const { userInfo, isLoading: userInfoLoading } = useUserInfo();
  const requiresSalespersonPin = !!posDetails?.custom_sales_person_pin_required;

  useEffect(() => {
    if (requiresSalespersonPin) {
      void ensureInitialized();
    }
  }, [requiresSalespersonPin, ensureInitialized]);

  const runWithSalespersonGate = (action: () => void | Promise<void>) => {
    if (!requiresSalespersonPin || activeSalesperson) {
      void action();
      return;
    }

    pendingSalespersonActionRef.current = () => {
      void action();
    };
    setShowSalespersonAuthModal(true);
  };

  const handleSalespersonAuthenticated = () => {
    const pendingAction = pendingSalespersonActionRef.current;
    pendingSalespersonActionRef.current = null;
    setShowSalespersonAuthModal(false);
    if (pendingAction) {
      pendingAction();
    }
  };
  
  const canProcessReturns = ![0, "0", false].includes(posDetails?.custom_allow_return as 0 | "0" | false);

  // Cross-cashier reading is the POS Profile's call, except for managers (by role), who see
  // every invoice of the company. Absent flag reads as off, matching the backend, so a site
  // that has not migrated keeps the behaviour it has today.
  const canViewOtherCashiers =
    posDetails?.custom_allow_viewing_other_cashiers === 1 || !!userInfo?.can_view_all_invoices;
  const currentUserCashier = userInfo?.full_name || "";

  // Force the cashier filter to the current (non-admin) user once fresh user
  // info has loaded. This also self-corrects any stale/leftover cashier name
  // that was persisted to localStorage (filters or user info) by a different
  // user on a shared device, instead of only seeding it once from "all".
  useEffect(() => {
    if (userInfoLoading) return;
    if (!canViewOtherCashiers && currentUserCashier && cashierFilter !== currentUserCashier) {
      setCashierFilter(currentUserCashier);
    }
  }, [canViewOtherCashiers, currentUserCashier, cashierFilter, userInfoLoading]);

  useEffect(() => {
    window.localStorage.setItem(INVOICE_HISTORY_VIEW_MODE_KEY, viewMode);
  }, [viewMode]);

  useEffect(() => {
    if (skipFilterPersist.current) {
      skipFilterPersist.current = false;
      return;
    }

    window.localStorage.setItem(
      INVOICE_HISTORY_FILTERS_KEY,
      JSON.stringify({
        activeTab,
        searchTerm,
        fromDate,
        toDate,
        paymentFilter,
        cashierFilter,
      })
    );
  }, [activeTab, searchTerm, fromDate, toDate, paymentFilter, cashierFilter]);

  // Keyboard event handler for Escape key
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (showCustomerSelection) {
          setShowCustomerSelection(false);
        }
        if (showMultiReturn) {
          setShowMultiReturn(false);
        }
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [showCustomerSelection, showMultiReturn]);

  const canQuote = quotesAllowed(posDetails);
  // The Quotes tab goes with the till's Allow Quotes box; a remembered Quotes tab falls back to All.
  useEffect(() => {
    if (!canQuote && activeTab === "Quotes") setActiveTab("all");
  }, [canQuote, activeTab]);

  const tabs = [
    { id: "all", name: "All Invoices", icon: FileText, color: "text-gray-600" },
    { id: "queued", name: "Queued", icon: Clock, color: "text-amber-600" },
    { id: "Held", name: "Held", icon: FilePlus, color: "text-gray-500" },
    { id: "Quotes", name: "Quotes", icon: FileSignature, color: "text-sky-600" },
    { id: "Unpaid", name: "Unpaid", icon: Clock, color: "text-yellow-600" },
    { id: "Partly Paid", name: "Partly Paid", icon: AlertTriangle, color: "text-orange-600" },
    { id: "Paid", name: "Paid", icon: CheckCircle, color: "text-green-600" },
    { id: "Overdue", name: "Overdue", icon: XCircle, color: "text-red-600" },
    { id: "Return", name: "Returns", icon: RefreshCw, color: "text-purple-600" },
    { id: "Cancelled", name: "Cancelled", icon: XCircle, color: "text-red-500" },
  ].filter((tab) => canQuote || tab.id !== "Quotes");

  // Invoices are already bounded by the server; this also bounds the Held tab's orders.
  const filterInvoiceByDate = (invoiceDateStr: string) => inDateRange(invoiceDateStr, fromDate, toDate);


const getStatusBadge = (status: string) => {
  const baseClasses = "px-2 py-1 rounded-full text-xs font-medium";
  const normalized = status?.toLowerCase() || "";

  switch (normalized) {
    // Payment statuses
    case "paid":
      return `${baseClasses} bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400`;
    case "approval pending":
    case "unpaid":
      return `${baseClasses} bg-yellow-100 text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400`;
    case "partly paid":
      return `${baseClasses} bg-orange-100 text-orange-800 dark:bg-orange-900/20 dark:text-orange-400`;
    case "overdue":
    case "expired":
      return `${baseClasses} bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400`;
    case "partially ordered":
    case "price approved":
    case "ordered":
      return `${baseClasses} bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400`;
    case "needs approval":
    case "lost":
      return `${baseClasses} bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400`;
    case "draft":
    case "held":
    case "open":
      return `${baseClasses} bg-gray-100 text-gray-800 dark:bg-gray-900/20 dark:text-gray-400`;
    case "return":
      return `${baseClasses} bg-purple-100 text-purple-800 dark:bg-purple-900/20 dark:text-purple-400`;
    case "cancelled":
      return `${baseClasses} bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400`;

    // ZATCA submission statuses
    case "pending":
      return `${baseClasses} bg-yellow-100 text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400`;
    case "reported":
      return `${baseClasses} bg-blue-100 text-blue-800 dark:bg-blue-900/20 dark:text-blue-400`;
    case "not reported":
      return `${baseClasses} bg-gray-100 text-gray-800 dark:bg-gray-900/20 dark:text-gray-400`;
    case "cleared":
      return `${baseClasses} bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400`;
    case "not cleared":
      return `${baseClasses} bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400`;

    default:
      return `${baseClasses} bg-gray-100 text-gray-800 dark:bg-gray-900/20 dark:text-gray-400`; // Neutral fallback
  }
};

const APPROVAL_BADGE_TONE_CLASSES: Record<string, string> = {
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/20 dark:text-amber-400",
  green: "bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400",
  red: "bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400",
};

const QUEUE_STATUS_BADGE: Record<string, string> = {
  failed: "bg-rose-100 text-rose-800 dark:bg-rose-900/20 dark:text-rose-400",
  processing: "bg-blue-100 text-blue-800 dark:bg-blue-900/20 dark:text-blue-400",
  queued: "bg-amber-100 text-amber-800 dark:bg-amber-900/20 dark:text-amber-400",
};

const renderQueueStatusBadge = (invoice: SalesInvoice & { queueStatus?: string }) => {
  const label = (invoice.queueStatus || "").trim();
  const tone = QUEUE_STATUS_BADGE[label.toLowerCase()];
  if (!tone) return null;
  return <span className={`px-2 py-1 rounded-full text-xs font-medium ${tone}`}>{label}</span>;
};

/** Beside a held order's status: an M-Pesa request went out for it. */
const renderMpesaBadge = (invoice: SalesInvoice & HeldOrderExtras) =>
  invoice.isHeldOrder && invoice.mpesaOrder ? (
    <span
      className={`ml-2 px-2 py-1 rounded-full text-xs font-medium ${APPROVAL_BADGE_TONE_CLASSES.green}`}
      title="An M-Pesa request was sent for this order. Open it to finish the sale - don't charge the customer again."
    >
      M-Pesa
    </span>
  ) : null;



  const heldStatuses = useMemo(() => [...new Set(heldOrders.map((o) => o.status))].sort(), [heldOrders]);
  // A status no held order carries any more (approved since) stops filtering.
  const heldStatus = heldStatuses.includes(heldStatusFilter) ? heldStatusFilter : "all";

  const filteredInvoices = useMemo(() => {
    if (isLoading) return [];
    if (error) return [];

    const source = rowsForTab(activeTab, { invoices, heldOrders, quotes });

    const filtered = source.filter((invoice) => {
      // Server-side search is handled by the API, so we only apply client-side filters
      // Normalize status comparison to handle case and whitespace differences
      const matchesStatus = matchesInvoiceTab(invoice, activeTab);
      const matchesPayment = matchesPaymentFilter(invoice, paymentFilter);
      const matchesCashier = cashierFilter === "all" || !!(invoice as SalesInvoice & HeldOrderExtras).isQuote || invoice.cashier === cashierFilter;
      const matchesDate = filterInvoiceByDate(invoice.date);

      const matchesHeldStatus = activeTab !== "Held" || heldStatus === "all" || invoice.status === heldStatus;

      return matchesPayment && matchesCashier && matchesStatus && matchesDate && matchesHeldStatus;
    });

    return activeTab === "queued" ? sortFailedFirst(filtered) : filtered;
  }, [invoices, heldOrders, quotes, activeTab, fromDate, toDate, paymentFilter, cashierFilter, heldStatus, isLoading, error, filterInvoiceByDate]);

  const { sortedData: sortedInvoices, sortKey: invoiceSortKey, sortDirection: invoiceSortDirection, toggleSort: toggleInvoiceSort } = useTableSort(
    filteredInvoices,
    {
      id: (invoice) => invoice.id,
      date: (invoice) => toSortableTimestamp(invoice.date, invoice.time),
      customer: (invoice) => invoice.customer,
      cashier: (invoice) => invoice.cashier,
      paymentMethod: (invoice) => invoice.paymentMethod,
      amount: (invoice) => invoice.totalAmount,
      status: (invoice) => invoice.status,
    }
  );

  const uniqueCashiers = useMemo(() => {
    return [...new Set(invoices.map(invoice => invoice.cashier).filter(Boolean))];
  }, [invoices]);

    // Filter customers based on search query
  const filteredCustomers = useMemo(() => {
    if (!customers) return [];

    // First, filter out customers with invalid data
    const validCustomers = customers.filter(customer =>
      customer && (customer.customer_name || customer.name)
    );

    if (!customerSearchQuery.trim()) return validCustomers;

    const query = customerSearchQuery.toLowerCase();
    return validCustomers.filter(customer => {
      // Safely handle potentially undefined values
      const customerName = customer.customer_name?.toLowerCase() || '';
      const customerCode = customer.name?.toLowerCase() || '';

      return customerName.includes(query) || customerCode.includes(query);
    });
  }, [customers, customerSearchQuery]);

  // Get count for each status - filtered by cashier, date, and payment (but not status)
  // This ensures tab counts reflect the current filter selections
  const getStatusCount = (status: string) => {
    const countSource = rowsForTab(status, { invoices, heldOrders, quotes });

    // First apply all filters except status
    const invoicesFilteredByOtherFilters = countSource.filter((invoice) => {
      const matchesPayment = matchesPaymentFilter(invoice, paymentFilter);
      const matchesCashier = cashierFilter === "all" || !!(invoice as SalesInvoice & HeldOrderExtras).isQuote || invoice.cashier === cashierFilter;
      const matchesDate = filterInvoiceByDate(invoice.date);
      return matchesPayment && matchesCashier && matchesDate;
    });

    return invoicesFilteredByOtherFilters.filter((invoice) => matchesInvoiceTab(invoice, status));
  };

  // Queued's count also says how many of them failed, so a failure still stands out now
  // that Failed Queue is part of Queued.
  const renderTabCount = (tabId: string, badgeClass: string) => {
    const rows = getStatusCount(tabId);
    const failed = tabId === "queued" ? countFailed(rows) : 0;
    return (
      <>
        <span className={badgeClass}>{rows.length}</span>
        {failed > 0 && (
          <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300">
            {failed} failed
          </span>
        )}
      </>
    );
  };

  // Loading state - only block if nothing loaded yet
  if ((isLoading && invoices.length === 0) || userInfoLoading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-beveren-600 mx-auto"></div>
          <p className="mt-4 text-gray-600 dark:text-gray-300">Loading invoices...</p>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="bg-red-50 dark:bg-red-900/20 p-2 rounded-lg max-w-md">
          <h3 className="text-lg font-medium text-red-800 dark:text-red-200">Error loading invoices</h3>
          <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200 rounded hover:bg-red-200 dark:hover:bg-red-800"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // Filters are remembered across sessions, so a stale date or name can hide a whole tab
  // (a cashier on Allparts lost every held order to one). One click puts them back.
  const filtersActive = Boolean(
    searchTerm || heldStatus !== "all" || fromDate || toDate || paymentFilter !== "all" ||
      (canViewOtherCashiers && cashierFilter !== "all")
  );
  const clearFilters = () => {
    setSearchTerm("");
    setHeldStatusFilter("all");
    setFromDate("");
    setToDate("");
    setPaymentFilter("all");
    // A user held to their own sales is put back on themselves by the effect above.
    setCashierFilter("all");
  };

  // Define render functions before they are used
  const renderFilters = () => (
    <div className="w-full max-w-none bg-white/95 dark:bg-gray-800/95 rounded-xl p-3 border border-gray-200 dark:border-gray-700 mb-4 backdrop-blur">
      {/* One line on a wide screen; wraps as the window narrows, stacks on a phone. */}
      <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-2">
        <div className="relative sm:flex-1 sm:min-w-[12rem]">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" size={14} />
          <input
            type="text"
            placeholder="Search number, customer, cashier, amount, date..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-white"
          />
          {isLoading && invoices.length > 0 && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2">
              <div className="animate-spin h-4 w-4 border-2 border-b-transparent border-beveren-500 rounded-full"></div>
            </div>
          )}
        </div>
<DateRangeFilter
          from={fromDate}
          to={toDate}
          onChange={(from, to) => {
            setFromDate(from);
            setToDate(to);
          }}
          className="sm:order-last"
        />
        
        <select
          value={cashierFilter}
          onChange={(e) => setCashierFilter(e.target.value)}
          disabled={!canViewOtherCashiers}
          className={`sm:w-auto px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-white ${
            !canViewOtherCashiers ? 'opacity-50 cursor-not-allowed' : ''
          }`}
        >
          <option value="all">All Cashiers</option>
          {uniqueCashiers.map((cashier) => (
            <option key={cashier} value={cashier}>
              {cashier}
            </option>
          ))}
        </select>
        {!canViewOtherCashiers && (
          <p className="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">Showing only your transactions</p>
        )}
        {activeTab === "Quotes" && (
          <div role="group" aria-label="Quote state" className="flex rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden text-sm">
            {(["open", "converted", "closed", "all"] as const).map((st) => (
              <button key={st} type="button" aria-pressed={quoteState === st} onClick={() => setQuoteState(st)}
                className={`px-3 py-1.5 capitalize ${quoteState === st ? "bg-beveren-600 text-white" : "bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-600"}`}>
                {st === "converted" ? "ordered" : st}
              </button>
            ))}
          </div>
        )}
        {activeTab === "Held" && heldStatuses.length > 1 && (
          <select
            aria-label="Status"
            value={heldStatus}
            onChange={(e) => setHeldStatusFilter(e.target.value)}
            className="sm:w-auto px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-white"
          >
            <option value="all">All Statuses</option>
            {heldStatuses.map((st) => (
              <option key={st} value={st}>{st}</option>
            ))}
          </select>
        )}
        {/* Held orders and quotes have no payment yet, and the filter never applies to them. */}
        {activeTab !== "Held" && activeTab !== "Quotes" && (
          <select
            value={paymentFilter}
            onChange={(e) => setPaymentFilter(e.target.value)}
            className="sm:w-auto px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-beveren-500 bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-white"
          >
            <option value="all">All Payments</option>
            {modes.map((mode) => (
              <option key={mode.name} value={mode.name}>
                {mode.name}
              </option>
            ))}
          </select>
        )}
        {filtersActive && (
          <button
            type="button"
            onClick={clearFilters}
            title="Clear filters"
            aria-label="Clear filters"
            className="sm:order-last self-start sm:self-auto p-1.5 rounded-lg border border-gray-300 dark:border-gray-600 text-gray-500 hover:text-red-600 hover:border-red-300 dark:text-gray-400 dark:hover:text-red-400"
          >
            <X size={16} />
          </button>
        )}
      </div>
        {hasMore && pagesInvoices && (
          <div className="mt-3 text-center">
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Search works on all invoices in the database. Load more invoices to see additional results.
            </p>
          </div>
        )}
    </div>
  );

  const renderInvoicesTable = () => (
    <div className="w-full max-w-none bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="flex items-center justify-between p-2 border-b border-gray-200 dark:border-gray-700">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
          {activeTab === "all" ? "All Invoices" : tabs.find(t => t.id === activeTab)?.name} ({filteredInvoices.length})
        </h3>
        <div className="flex items-center space-x-2 bg-gray-100 dark:bg-gray-700 rounded-lg p-1">
          <button
            onClick={() => setViewMode("list")}
            className={`p-2 rounded-md transition-colors ${
              viewMode === "list"
                ? "bg-white dark:bg-gray-600 text-beveren-600 dark:text-beveren-400 shadow-sm"
                : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
            }`}
          >
            <List className="w-4 h-4" />
          </button>
          <button
            onClick={() => setViewMode("cards")}
            className={`p-2 rounded-md transition-colors ${
              viewMode === "cards"
                ? "bg-white dark:bg-gray-600 text-beveren-600 dark:text-beveren-400 shadow-sm"
                : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
            }`}
          >
            <Grid3X3 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {viewMode === "list" ? (
        <div className="overflow-x-auto">
          <table className="w-full table-auto">
            <thead className="bg-gray-50 dark:bg-gray-700">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  <SortableHeaderButton label="Invoice" sortKey="id" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                </th>
                {/* Up front: the table scrolls under the pinned Actions, so a badge by Status went unseen.
                    On Held that is the Status column itself, which says where a price approval stands. */}
                {activeTab === "queued" && (
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider whitespace-nowrap">
                    Queue Status
                  </th>
                )}
                {activeTab === "Held" && (
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    <SortableHeaderButton label="Status" sortKey="status" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                  </th>
                )}
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider whitespace-nowrap">
                  <SortableHeaderButton label="Date" sortKey="date" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                </th>
                <th className="px-6 py-3 w-48 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  <SortableHeaderButton label="Customer" sortKey="customer" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  <SortableHeaderButton label="Cashier" sortKey="cashier" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                </th>
                <th className="px-6 py-3 w-40 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  <SortableHeaderButton label="Payment" sortKey="paymentMethod" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  <SortableHeaderButton label="Amount" sortKey="amount" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                </th>
                {activeTab !== "Held" && (
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    <SortableHeaderButton label="Status" sortKey="status" activeKey={invoiceSortKey} direction={invoiceSortDirection} onSort={toggleInvoiceSort} />
                  </th>
                )}
                {posDetails?.is_zatca_enabled && (
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Zatca Status
                  </th>
                )}
                <th className="px-6 py-3 sticky right-0 z-10 bg-gray-50 dark:bg-gray-700 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-600">
              {sortedInvoices.map((invoice) => (
                <tr key={`${activeTab}-${invoice.id}`} className="group hover:bg-gray-50 dark:hover:bg-gray-700">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="text-sm font-medium text-gray-900 dark:text-white">{invoice.id}</div>
                  </td>
                  {activeTab === "queued" && (
                    <td className="px-6 py-4 whitespace-nowrap">{renderQueueStatusBadge(invoice)}</td>
                  )}
                  {activeTab === "Held" && (
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={getStatusBadge(invoice.status)}>{invoice.status}</span>
                      {renderMpesaBadge(invoice)}
                    </td>
                  )}
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="text-sm text-gray-900 dark:text-white">{formatDateTime(invoice.date, invoice.time)}</div>
                  </td>
                  <td className="px-6 py-4 max-w-[12rem]">
                    <div title={invoice.customer} className="block truncate text-sm text-gray-900 dark:text-white">{invoice.customer}</div>
                    {walkinSubline(invoice.customer, invoice.walkinName) && (
                      <div title={invoice.walkinName} className="block truncate text-xs text-gray-500 dark:text-gray-400">
                        {walkinSubline(invoice.customer, invoice.walkinName)}
                      </div>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-white">
                    {invoice.cashier}
                  </td>
                  <td className="px-6 py-4 max-w-[10rem]">
                    <span title={invoice.paymentMethod} className="block truncate text-sm text-gray-900 dark:text-white">{invoice.paymentMethod}</span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="text-sm font-medium text-gray-900 dark:text-white">
                      {formatCurrencyWithSymbol(invoice.totalAmount, invoice.currency)}
                    </div>
                    {inStockLine(invoice)}
                    {invoice.giftCardDiscount > 0 && (
                      <div className="text-xs text-orange-600 dark:text-green-400">
                        -{formatCurrencyWithSymbol(invoice.giftCardDiscount, invoice.currency)} discount
                      </div>
                    )}
                  </td>
                  {activeTab !== "Held" && (
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={getStatusBadge(invoice.status)}>{invoice.status}</span>
                    </td>
                  )}
                  {posDetails?.is_zatca_enabled && (
                    <td className="px-6 py-4 whitespace-nowrap">
                                  {/* @ts-expect-error just ignore */}
                      <span className={getStatusBadge(invoice.custom_zatca_submit_status)}>{invoice.custom_zatca_submit_status}</span>
                    </td>
                  )}
                  <td className="px-6 py-4 sticky right-0 z-10 whitespace-nowrap bg-white dark:bg-gray-800 group-hover:bg-gray-50 dark:group-hover:bg-gray-700 text-sm font-medium">
                    <div className="flex space-x-2">
                      {/* A row the till will not open (rung by another cashier) gets no View or
                          Return, as on the Closing Shift page: the detail read behind both is refused. */}
                      {(invoice as SalesInvoice & HeldOrderExtras).isQuote ? (
                        renderQuoteButtons(invoice, "sm")
                      ) : (invoice as SalesInvoice & HeldOrderExtras).isHeldOrder && invoice.canOpen !== false ? (
                        renderHeldOrderShareButtons(invoice.id || invoice.name, "sm")
                      ) : invoice.canOpen !== false ? (
                        <>
                          <button
                            onClick={() => handleViewInvoice(invoice)}
                            className="text-beveren-600 hover:text-beveren-900 flex items-center space-x-1"
                          >
                            <Eye className="w-4 h-4" />
                            <span>View</span>
                          </button>
                          <DocShareButtons doctype="Sales Invoice" name={invoice.id || invoice.name} size="sm" posDetails={posDetails} copyOnly />
                        </>
                      ) : (
                        <span className="text-gray-400 text-xs" title="Rung by another cashier">—</span>
                      )}
                      {(invoice.status === "Draft" || (invoice as SalesInvoice & { isHeldOrder?: boolean }).isHeldOrder) && (
                        <>
                          <button
                            onClick={() => void handleGoToCart(invoice)}
                            className="text-blue-600 hover:text-blue-900 dark:text-blue-400 dark:hover:text-blue-300 flex items-center space-x-1"
                          >
                            <Edit className="w-4 h-4" />
                            <span>Edit</span>
                          </button>
                          <button
                            onClick={() => void handleSubmitDirect(invoice)}
                            className="text-green-600 hover:text-green-900 dark:text-green-400 dark:hover:text-green-300 flex items-center space-x-1"
                          >
                            <Check className="w-4 h-4" />
                            <span>Submit</span>
                          </button>
                          {((invoice as SalesInvoice & HeldOrderExtras).approvalActions || []).map((action) => (
                            <button
                              key={action}
                              onClick={() => void handleApprovalAction(invoice.id || invoice.name, action)}
                              className={`${action === "Approve" ? "text-green-600 hover:text-green-900 dark:text-green-400" : "text-red-600 hover:text-red-900 dark:text-red-400"} flex items-center space-x-1`}
                            >
                              {action === "Approve" ? <Check className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
                              <span>{action === "Approve" ? "Approve prices" : "Reject"}</span>
                            </button>
                          ))}
                          {(invoice as SalesInvoice & HeldOrderExtras).approvalState === PRICE_APPROVED && (
                            <button
                              onClick={() => setWithdrawTarget(invoice.id || invoice.name)}
                              className="text-red-600 hover:text-red-900 dark:text-red-400 dark:hover:text-red-300 flex items-center space-x-1"
                            >
                              <XCircle className="w-4 h-4" />
                              <span>Withdraw approval</span>
                            </button>
                          )}
                        </>
                      )}
                      {/* @ts-expect-error just ignore */}
                      {invoice.canOpen !== false && canProcessReturns && ["Paid", "Unpaid", "Overdue", "Partly Paid", "Credit Note Issued"].includes(invoice.status) && !invoice.is_return && hasReturnableItems(invoice) && (

                        <button
                          onClick={() => handleSingleReturnClick(invoice)}
                          className="text-orange-600 hover:text-orange-900 flex items-center space-x-1"
                        >
                          <RotateCcw className="w-4 h-4" />
                          <span>Return</span>
                        </button>
                      )}

                      {((invoice as SalesInvoice & { queueStatus?: string }).queueStatus || "").toLowerCase() === "failed" && (
                        <button
                          onClick={() => handleRetryQueue(invoice)}
                          className="text-blue-600 hover:text-blue-900 flex items-center space-x-1"
                        >
                          <RefreshCw className="w-4 h-4" />
                          <span>Retry</span>
                        </button>
                      )}

                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 p-2">
          {filteredInvoices.map((invoice) => (
            <div
              key={`${activeTab}-${invoice.id}`}
              className="bg-gray-50 dark:bg-gray-700 rounded-lg p-4 border border-gray-200 dark:border-gray-600 hover:shadow-md transition-shadow"
            >
              <div className="flex items-center justify-between mb-3">
                <div className="text-sm font-medium text-gray-900 dark:text-white">{invoice.id}</div>
                <div className="flex items-center gap-1">
                  <span className={getStatusBadge(invoice.status)}>{invoice.status}</span>
                  {renderMpesaBadge(invoice)}
                  {activeTab === "queued" && renderQueueStatusBadge(invoice)}
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600 dark:text-gray-400">Customer:</span>
                  <span className="text-gray-900 dark:text-white text-right">
                    {invoice.customer}
                    {walkinSubline(invoice.customer, invoice.walkinName) && (
                      <span className="block text-xs text-gray-500 dark:text-gray-400">
                        {walkinSubline(invoice.customer, invoice.walkinName)}
                      </span>
                    )}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600 dark:text-gray-400">Amount:</span>
                  <span className="text-right">
                    <span className="font-medium text-gray-900 dark:text-white">{formatCurrencyWithSymbol(invoice.totalAmount, invoice.currency)}</span>
                    {inStockLine(invoice)}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600 dark:text-gray-400">Date:</span>
                  <span className="text-gray-900 dark:text-white">{invoice.date}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600 dark:text-gray-400">Cashier:</span>
                  <span className="text-gray-900 dark:text-white">{invoice.cashier}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600 dark:text-gray-400">POS Profile:</span>
                  <span className="text-gray-900 dark:text-white">{invoice.posProfile}</span>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {(invoice as SalesInvoice & HeldOrderExtras).isQuote ? (
                  <div className="flex items-center gap-1">{renderQuoteButtons(invoice, "md")}</div>
                ) : (invoice as SalesInvoice & HeldOrderExtras).isHeldOrder && invoice.canOpen !== false ? (
                  <div className="flex items-center gap-1">{renderHeldOrderShareButtons(invoice.id || invoice.name, "md")}</div>
                ) : invoice.canOpen !== false ? (
                  <>
                    <button
                      onClick={() => handleViewInvoice(invoice)}
                      className="flex-1 text-xs px-3 py-2 bg-beveren-600 text-white rounded hover:bg-beveren-700 transition-colors"
                    >
                      View
                    </button>
                    <div className="flex items-center gap-1">
                      <DocShareButtons doctype="Sales Invoice" name={invoice.id || invoice.name} size="md" posDetails={posDetails} copyOnly />
                    </div>
                  </>
                ) : (
                  <span className="flex-1 text-xs px-3 py-2 text-center text-gray-400" title="Rung by another cashier">—</span>
                )}
                {(invoice.status === "Draft" || (invoice as SalesInvoice & { isHeldOrder?: boolean }).isHeldOrder) && (
                  <>
                    <button
                      onClick={() => void handleGoToCart(invoice)}
                      className="flex-1 text-xs px-3 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors flex items-center justify-center space-x-1"
                    >
                      <Edit className="w-3 h-3" />
                      <span>Edit</span>
                    </button>
                    <button
                      onClick={() => void handleSubmitDirect(invoice)}
                      className="flex-1 text-xs px-3 py-2 bg-green-600 text-white rounded hover:bg-green-700 transition-colors flex items-center justify-center space-x-1"
                    >
                      <Check className="w-3 h-3" />
                      <span>Submit</span>
                    </button>
                    {((invoice as SalesInvoice & HeldOrderExtras).approvalActions || []).map((action) => (
                      <button
                        key={action}
                        onClick={() => void handleApprovalAction(invoice.id || invoice.name, action)}
                        className={`flex-1 text-xs px-3 py-2 ${action === "Approve" ? "bg-green-600 hover:bg-green-700" : "bg-red-600 hover:bg-red-700"} text-white rounded transition-colors flex items-center justify-center space-x-1`}
                      >
                        {action === "Approve" ? <Check className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                        <span>{action === "Approve" ? "Approve" : "Reject"}</span>
                      </button>
                    ))}
                    {(invoice as SalesInvoice & HeldOrderExtras).approvalState === PRICE_APPROVED && (
                      <button
                        onClick={() => setWithdrawTarget(invoice.id || invoice.name)}
                        className="flex-1 text-xs px-3 py-2 bg-red-600 text-white rounded hover:bg-red-700 transition-colors flex items-center justify-center space-x-1"
                      >
                        <XCircle className="w-3 h-3" />
                        <span>Withdraw</span>
                      </button>
                    )}
                  </>
                )}
                  {invoice.canOpen !== false && canProcessReturns && ["Paid", "Unpaid", "Overdue", "Partly Paid", "Credit Note Issued"].includes(invoice.status) && !(invoice as SalesInvoice & { is_return?: boolean | number }).is_return && hasReturnableItems(invoice) && (
                  <button
                    onClick={() => handleSingleReturnClick(invoice)}
                    className="flex-1 text-xs px-3 py-2 bg-orange-600 text-white rounded hover:bg-orange-700 transition-colors"
                  >
                    Return
                  </button>
                )}
                {((invoice as SalesInvoice & { queueStatus?: string }).queueStatus || "").toLowerCase() === "failed" && (
                  <button
                    onClick={() => handleRetryQueue(invoice)}
                    className="flex-1 text-xs px-3 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors flex items-center justify-center space-x-1"
                  >
                    <RefreshCw className="w-3 h-3" />
                    <span>Retry</span>
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {quoteToShare && (
        <QuoteSavedDialog
          title="Send quote"
          quoteName={quoteToShare.name}
          customerName={quoteToShare.customer}
          phone=""
          posDetails={posDetails as Record<string, unknown> | null}
          onDone={() => setQuoteToShare(null)}
        />
      )}

      {/* Load More Button */}
      {hasMore && pagesInvoices && (
        <div className="flex justify-center mt-8">
          <button
            onClick={loadMore}
            disabled={isLoadingMore}
            className={`px-6 py-3 rounded-lg font-medium transition-colors ${
              isLoadingMore
                ? 'bg-gray-400 text-gray-200 cursor-not-allowed'
                : 'bg-beveren-600 text-white hover:bg-beveren-700'
            }`}
          >
            {isLoadingMore ? (
              <div className="flex items-center space-x-2">
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                <span>Loading...</span>
              </div>
            ) : (
              `Load More (${totalLoaded}/${totalCount})`
            )}
          </button>
        </div>
      )}

      {/* Show message when all invoices are loaded */}
      {!hasMore && pagesInvoices && totalLoaded > 0 && (
        <div className="text-center mt-8 py-4">
          <p className="text-gray-600 dark:text-gray-400">
            {filteredInvoices.length > 0
              ? `Showing ${filteredInvoices.length} invoice${filteredInvoices.length !== 1 ? 's' : ''} (${totalLoaded} total loaded)`
              : `All ${totalCount} invoices loaded`
            }
          </p>
        </div>
      )}
    </div>
  );

  const handleViewInvoice = (invoice: SalesInvoice) => {
    const isHeldOrder = !!(invoice as SalesInvoice & { isHeldOrder?: boolean }).isHeldOrder;
    if (isHeldOrder) {
      if (heldOrderPrintBlocked()) {
        toast.warn("Printing held orders is blocked on this till");
        return;
      }
      // Held drafts are Sales Order docs; there is no in-app detail view for
      // them, so fall back to ERPNext's normal print route with the
      // doctype's default print format.
      handlePrintInvoice(invoice, { doctype: "Sales Order", posDetails });
      return;
    }
    navigate(`/invoice/${invoice.id}`);
  };


    // Helper function to check if invoice has items that can still be returned
  const hasReturnableItems = (invoice: SalesInvoice) => {
    if (!invoice || !invoice.items) {
      return false;
    }

    // Use the canReturn property set by background return data loading
    // @ts-expect-error just ignore
    if (invoice.canReturn !== undefined) {
      //@ts-expect-error just ignore
      return invoice.canReturn;
    }

    // Fallback to old logic if canReturn is not set yet
    const hasReturnable = invoice.items.some(item => {
      const soldQty = item.qty || item.quantity || 0;
      const returnedQty = item.returned_qty || 0;
      const canReturn = returnedQty < soldQty;
      return canReturn;
    });

    return hasReturnable;
  };

  const handleGoToCart = async (invoice: SalesInvoice) => {
    const draftInvoiceId = invoice.id || invoice.name;
    if (!draftInvoiceId) {
      toast.error("Unable to edit draft invoice: missing invoice identifier");
      return;
    }

    if (requiresSalespersonPin && !activeSalesperson) {
      runWithSalespersonGate(() => handleGoToCart(invoice));
      return;
    }

    try {
      const isHeldOrder = !!(invoice as SalesInvoice & { isHeldOrder?: boolean }).isHeldOrder;
      // Items already in the cart from something else: replace them, or merge this order in.
      if (isHeldOrder && cartItems.length > 0 && getOriginalHeldOrderId() !== draftInvoiceId) {
        setMergeAsk({ id: draftInvoiceId, data: await fetchHeldOrder(draftInvoiceId), step: "how" });
        return;
      }
      await openInCart(draftInvoiceId, isHeldOrder);
      //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
      console.error("Error going to cart:", error);
      toast.error(error.message || "Failed to add items to cart");
    }
  };

  const openInCart = async (id: string, isHeldOrder: boolean) => {
    const success = isHeldOrder ? await addHeldOrderToCart(id) : await addDraftInvoiceToCart(id);
    if (success) {
      setTimeout(() => {
        navigate('/pos'); // Navigate directly to POS page
      }, 500);
    }
  };

  const finishMerge = (combine: boolean) => {
    if (!mergeAsk) return;
    mergeHeldOrderIntoCart(mergeAsk.id, mergeAsk.data, combine);
    setMergeAsk(null);
    navigate('/pos');
  };

  const handleSubmitDirect = async (invoice: SalesInvoice) => {
    const draftInvoiceId = invoice.id || invoice.name;
    if (!draftInvoiceId) {
      toast.error("Unable to submit draft invoice: missing invoice identifier");
      return;
    }

    if (requiresSalespersonPin && !activeSalesperson) {
      runWithSalespersonGate(() => handleSubmitDirect(invoice));
      return;
    }

    try {
      const isHeldOrder = !!(invoice as SalesInvoice & { isHeldOrder?: boolean }).isHeldOrder;
      const success = isHeldOrder
        ? await addHeldOrderToCart(draftInvoiceId)
        : await addDraftInvoiceToCart(draftInvoiceId);
      if (success) {
        await loadCachedItemsToCart();
        setShowDraftPaymentDialog(true);
      }
      //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
      console.error("Error opening payment dialog for draft invoice:", error);
      const errorMessage = extractErrorFromException(error, "Failed to load invoice for payment");
      toast.error(errorMessage);
    }
  };

  const handleApprovalAction = async (orderId: string, action: string) => {
    try {
      const result = await applyHeldOrderAction(orderId, action);
      if (!result?.success) throw new Error((result as { message?: string })?.message || `Could not ${action.toLowerCase()} ${orderId}`);
      toast.success(action === "Approve" ? `Prices on ${orderId} approved` : `Approval for ${orderId} rejected`);
      await refetchHeldOrders();
      //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
      toast.error(error?.message || `Could not ${action.toLowerCase()} ${orderId}`);
    }
  };

  const handleWithdrawApproval = async () => {
    if (!withdrawTarget) return;
    setIsWithdrawingApproval(true);
    try {
      await applyHeldOrderAction(withdrawTarget, WITHDRAW_APPROVAL);
      toast.success("Price approval withdrawn");
      await refetchHeldOrders();
      //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
      toast.error(error?.message || "Failed to withdraw price approval");
    } finally {
      setIsWithdrawingApproval(false);
      setWithdrawTarget(null);
    }
  };

  const handleRefund = (invoiceId: string) => {
    handleReturnClick(invoiceId);
    setShowInvoiceModal(false);
  };

  const handleReturnClick = async (invoiceName: string) => {
    if (requiresSalespersonPin && !activeSalesperson) {
      runWithSalespersonGate(() => handleReturnClick(invoiceName));
    }

    if (!canProcessReturns) {
      toast.error("Returns are disabled for this POS Profile");
      return;
    }

    try {
      const result = await createSalesReturn(invoiceName);

      navigate(`/invoice/${result.return_invoice}`)
      toast.success(`Invoice returned: ${result.return_invoice}`);
      //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
      toast.error(error.message || "Failed to return invoice");
    }
  };

  const handleRetryQueue = async (invoice: SalesInvoice) => {
    try {
      await retryQueuedInvoice(invoice.id);
      toast.success(`Queued invoice ${invoice.id} sent back to the background worker`);
      window.location.reload();
      //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
      toast.error(error.message || "Failed to retry queued invoice");
    }
  };

  const handleCancel = (invoiceId: string) => {
    setShowInvoiceModal(false);
  };

      // Multi-Invoice Return handlers
    const handleMultiReturnClick = () => {
      if (requiresSalespersonPin && !activeSalesperson) {
        runWithSalespersonGate(handleMultiReturnClick);
      }
        
      if (!canProcessReturns) {
        toast.error("Returns are disabled for this POS Profile");
        return;
      }

      setSelectedCustomer("");
      setShowMultiReturn(true);
    };

    // Single Invoice Return handlers
    const handleSingleReturnClick = (invoice: SalesInvoice) => {
      if (requiresSalespersonPin && !activeSalesperson) {
        runWithSalespersonGate(() => handleSingleReturnClick(invoice));
      }
      if (!canProcessReturns) {
        toast.error("Returns are disabled for this POS Profile");
        return;
      }

      setSelectedInvoiceForReturn(invoice);
      setShowSingleReturn(true);
    };

    const handleSingleReturnSuccess = () => {
      setShowSingleReturn(false);
      setSelectedInvoiceForReturn(null);
      refetch();
    };


  const handleCustomerSelect = (customer: string) => {
    if (!customer) {
      toast.error("Invalid customer selection");
      return;
    }
    setSelectedCustomer(customer);
    setShowCustomerSelection(false);
    setCustomerSearchQuery("");
    setShowMultiReturn(true);
  };

  const handleMultiReturnSuccess = () => {
    // toast.success(`Created ${returnInvoices.length} return invoices successfully`);
    setShowMultiReturn(false);
    setSelectedCustomer("");
  };

  const handleCloseCustomerSelection = () => {
    setShowCustomerSelection(false);
    setCustomerSearchQuery("");
  };

  // Export functionality
  const handleExportInvoices = () => {
    try {
      if (!filteredInvoices || filteredInvoices.length === 0) {
        toast.error("No invoices to export");
        return;
      }

      // Convert invoices to exportable format
      const exportableInvoices: ExportableInvoice[] = filteredInvoices.map(invoice => {
        // Calculate outstanding amount: grand total - amount paid
        const grandTotal = invoice.totalAmount || 0;
        const amountPaid = invoice.amountPaid || 0;
        const outstandingAmount = Math.max(0, grandTotal - amountPaid);

        return {
          name: invoice.id || invoice.name || '',
          customer: invoice.customer || '',
          posting_date: invoice.date || invoice.posting_date || '',
          due_date: '', // Due date is not available in the current data structure
          grand_total: grandTotal,
          outstanding_amount: outstandingAmount,
          status: invoice.status || '',
          mode_of_payment: invoice.paymentMethod || '',
          currency: invoice.currency || 'SAR',
          company: invoice.company || ''
        };
      });

      // Generate filename based on current filters
      const tabName = activeTab === "all" ? "all" : activeTab.toLowerCase();
      const filename = getExportFilename(`invoices_${tabName}`, 'csv');

      // Export to CSV
      exportInvoicesToCSV(exportableInvoices, filename);

      toast.success(`Exported ${exportableInvoices.length} invoices successfully`);
      //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
      console.error('Export error:', error);
      toast.error(`Failed to export invoices: ${error.message}`);
    }
  };

  // Mobile layout: full-width content and persistent bottom navigation
  if (isMobile) {
    return (
      <div className="flex flex-col min-h-screen bg-gray-50 dark:bg-gray-900 font-inconsolata">
        {/* Mobile Header */}
        <div className="sticky top-0 z-20 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700">
          <div className="px-4 py-3">
            <div className="flex items-center justify-between">
              <h1 className="text-lg font-bold text-gray-900 dark:text-white">Invoice History</h1>
              <div className="flex items-center space-x-2">
                {MULTI_INVOICE_RETURN_ENABLED && canProcessReturns && <button
                    onClick={handleMultiReturnClick}
                    className="flex items-center space-x-2 px-3 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 transition-colors text-sm"
                  >
                  <Users className="w-4 h-4" />
                  <span>Multi Return</span>
                </button>}
                <button
                  onClick={handleExportInvoices}
                  className="flex items-center space-x-2 px-3 py-2 bg-beveren-600 text-white rounded-lg hover:bg-beveren-700 transition-colors text-sm"
                >
                  <Download className="w-4 h-4" />
                  <span>Export</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto pb-20 w-[98%] mx-auto px-2 py-4">
          <div className="sticky top-0 z-20 bg-gray-50/95 dark:bg-gray-900/95 backdrop-blur pb-2">
            {/* Status Tabs */}
            <div className="mb-6 w-full">
              <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
                <div className="border-b border-gray-200 dark:border-gray-700">
                  <nav className="-mb-px flex space-x-4 overflow-x-auto">
                    {tabs.map((tab) => (
                      <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`flex items-center space-x-2 py-2 px-1 border-b-2 font-medium text-xs whitespace-nowrap ${
                          activeTab === tab.id
                            ? "border-beveren-500 text-beveren-600 dark:text-beveren-400"
                            : `border-transparent ${tab.color} dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-300`
                        }`}
                      >
                        <tab.icon className="w-4 h-4" />
                        <span>{tab.name}</span>
                        {renderTabCount(tab.id, "ml-1 px-1.5 py-0.5 text-xs bg-gray-100 dark:bg-gray-700 rounded-full")}
                      </button>
                    ))}
                  </nav>
                </div>
              </div>
            </div>

            {renderFilters()}
          </div>
          {renderInvoicesTable()}
        </div>

        {/* Invoice View Modal */}
        <InvoiceViewModal
          invoice={selectedInvoice}
          isOpen={showInvoiceModal}
          onClose={() => setShowInvoiceModal(false)}
          onRefund={handleRefund}
          onCancel={handleCancel}
        />

        {/* Customer Selection Modal */}
        {showCustomerSelection && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
            <div className="bg-white dark:bg-gray-800 rounded-xl w-full max-w-md max-h-[90vh] flex flex-col">
              <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Select Customer</h2>
                  <button
                    onClick={handleCloseCustomerSelection}
                    className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                  >
                    <XCircle className="w-5 h-5" />
                  </button>
                </div>
              </div>
              <div className="p-2 flex-1 overflow-hidden flex flex-col">
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                  Choose a customer to process multi-invoice returns
                </p>

                {/* Search Bar */}
                <div className="relative mb-4">
                  <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" size={16} />
                  <input
                    type="text"
                    placeholder="Search customers by name or ID..."
                    value={customerSearchQuery}
                    onChange={(e) => setCustomerSearchQuery(e.target.value)}
                    className="w-full pl-10 pr-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  />
                </div>

                {/* Customer List */}
                <div className="flex-1 overflow-y-auto space-y-2">
                  {filteredCustomers && filteredCustomers.length > 0 ? (
                    filteredCustomers.map((customer) => (
                      <button

                        key={customer.name || customer.customer_name || Math.random()}
                        onClick={() => handleCustomerSelect(customer.name || customer.customer_name)}
                        className="w-full text-left p-3 rounded-lg border border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                      >
                        <div className="font-medium text-gray-900 dark:text-white">
                          {customer.customer_name || customer.name || 'Unknown Customer'}
                        </div>
                        <div className="text-sm text-gray-500 dark:text-gray-400">
                          {customer.name || customer.customer_name || 'No ID'}
                        </div>
                      </button>
                    ))
                  ) : customerSearchQuery.trim() ? (
                    <div className="text-center py-8">
                      <div className="text-gray-500 dark:text-gray-400">No customers found matching "{customerSearchQuery}"</div>
                    </div>
                  ) : (
                    <div className="text-center py-8">
                      <div className="text-gray-500 dark:text-gray-400">No customers found</div>
                    </div>
                  )}
                </div>

                {/* Results Counter */}
                {filteredCustomers && (
                  <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                    <div className="text-xs text-gray-500 dark:text-gray-400 text-center">
                      {customerSearchQuery.trim()
                        ? `${filteredCustomers.length} of ${customers?.length || 0} customers`
                        : `${customers?.length || 0} customers total`
                      }
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Multi-Invoice Return Modal */}
        <MultiInvoiceReturn
          customer={selectedCustomer}
          isOpen={MULTI_INVOICE_RETURN_ENABLED && showMultiReturn}
          onClose={() => setShowMultiReturn(false)}
          onSuccess={handleMultiReturnSuccess}
          // @ts-expect-error just ignore
          customers={customers}
        />


        {/* Bottom Navigation */}
        <BottomNavigation />
      </div>
    );
  }



  return (

    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex pb-2">
      <div className="flex-1 min-w-0 flex flex-col overflow-visible ml-20">
        {/* Header */}
        <div className="fixed top-0 left-20 right-0 z-50 bg-beveren-50 dark:bg-gray-800 shadow-sm border-b border-gray-200 dark:border-gray-700">
          <div className="h-16 px-6 flex items-center justify-between">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Invoice History</h1>
            <div className="flex items-center gap-2">
              {MULTI_INVOICE_RETURN_ENABLED && canProcessReturns && <button
                onClick={handleMultiReturnClick}
                className="flex items-center gap-2 px-4 py-2 text-sm font-semibold bg-orange-600 text-white rounded-lg hover:bg-orange-700 transition-colors"
              >
                <FileMinus className="w-4 h-4" />
                <span>Multi-Invoice Return</span>
              </button>}
              <button
                onClick={handleExportInvoices}
                className="flex items-center gap-2 px-4 py-2 text-sm font-semibold bg-beveren-600 text-white rounded-lg hover:bg-beveren-700 transition-colors"
              >
                <Download className="w-4 h-4" />
                <span>Export</span>
              </button>
            </div>
          </div>
        </div>

        <div className="flex-1 px-6 py-8 mt-16 max-w-none">
          <div className="sticky top-20 z-30 bg-gray-50/95 dark:bg-gray-900/95 backdrop-blur pb-2">
            {/* Status Tabs - Now full width like the table */}
            <div className="mb-8 w-full max-w-none">
              <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-2">
                <div className="border-b border-gray-200 dark:border-gray-700">
                  <nav className="-mb-px flex space-x-8 overflow-x-auto">
                    {tabs.map((tab) => (
                      <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`flex items-center space-x-2 py-2 px-1 border-b-2 font-medium text-sm whitespace-nowrap ${
                          activeTab === tab.id
                            ? "border-beveren-500 text-beveren-600 dark:text-beveren-400"
                            : `border-transparent ${tab.color} dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-300`
                        }`}
                      >
                        <tab.icon className="w-5 h-5" />
                        <span>{tab.name}</span>
                        {renderTabCount(tab.id, "ml-2 px-2 py-1 text-xs bg-gray-100 dark:bg-gray-700 rounded-full")}
                      </button>
                    ))}
                  </nav>
                </div>
              </div>
            </div>

            {/* Filters */}
            {renderFilters()}
          </div>

          {/* Invoices Table/Grid */}
          {renderInvoicesTable()}
        </div>

        {/* Invoice View Modal */}
        <InvoiceViewModal
          invoice={selectedInvoice}
          isOpen={showInvoiceModal}
          onClose={() => setShowInvoiceModal(false)}
          onRefund={handleRefund}
          onCancel={handleCancel}
        />

        {/* Customer Selection Modal */}
        {showCustomerSelection && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
            <div className="bg-white dark:bg-gray-800 rounded-xl w-full max-w-md max-h-[90vh] flex flex-col">
              <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Select Customer</h2>
                  <button
                    onClick={handleCloseCustomerSelection}
                    className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                  >
                    <XCircle className="w-5 h-5" />
                  </button>
                </div>
              </div>
              <div className="p-2 flex-1 overflow-hidden flex flex-col">
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                  Choose a customer to process multi-invoice returns
                </p>

                {/* Search Bar */}
                <div className="relative mb-4">
                  <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" size={16} />
                  <input
                    type="text"
                    placeholder="Search customers by name or ID..."
                    value={customerSearchQuery}
                    onChange={(e) => setCustomerSearchQuery(e.target.value)}
                    className="w-full pl-10 pr-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  />
                </div>

                {/* Customer List */}
                <div className="flex-1 overflow-y-auto space-y-2">
                  {filteredCustomers && filteredCustomers.length > 0 ? (
                    filteredCustomers.map((customer) => (
                      <button
                        key={customer.name || customer.customer_name || Math.random()}
                        onClick={() => handleCustomerSelect(customer.name || customer.customer_name)}
                        className="w-full text-left p-3 rounded-lg border border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                      >
                        <div className="font-medium text-gray-900 dark:text-white">
                          {customer.customer_name || customer.name || 'Unknown Customer'}
                        </div>
                        <div className="text-sm text-gray-500 dark:text-gray-400">
                          {customer.name || customer.customer_name || 'No ID'}
                        </div>
                      </button>
                    ))
                  ) : customerSearchQuery.trim() ? (
                    <div className="text-center py-8">
                      <div className="text-gray-500 dark:text-gray-400">No customers found matching "{customerSearchQuery}"</div>
                    </div>
                  ) : (
                    <div className="text-center py-8">
                      <div className="text-gray-500 dark:text-gray-400">No customers found</div>
                    </div>
                  )}
                </div>

                {/* Results Counter */}
                {filteredCustomers && (
                  <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                    <div className="text-xs text-gray-500 dark:text-gray-400 text-center">
                      {customerSearchQuery.trim()
                        ? `${filteredCustomers.length} of ${customers?.length || 0} customers`
                        : `${customers?.length || 0} customers total`
                      }
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Multi-Invoice Return Modal */}
        <MultiInvoiceReturn
          customer={selectedCustomer}
          isOpen={MULTI_INVOICE_RETURN_ENABLED && showMultiReturn}
          onClose={() => setShowMultiReturn(false)}
          onSuccess={handleMultiReturnSuccess}
          // @ts-expect-error just ignore
          customers={customers}
        />


        {/* Single Invoice Return Modal */}
        <SingleInvoiceReturn
          invoice={selectedInvoiceForReturn}
          isOpen={showSingleReturn}
          onClose={() => setShowSingleReturn(false)}
          onSuccess={handleSingleReturnSuccess}
        />
        <SalespersonAuthModal
          isOpen={showSalespersonAuthModal}
          onClose={() => {
            setShowSalespersonAuthModal(false);
            pendingSalespersonActionRef.current = null;
          }}
          onAuthenticated={handleSalespersonAuthenticated}
          allowDismiss
          title="Verify salesperson"
          description="Verify the salesperson before continuing this invoice action."
        />

        <ChoiceDialog
          isOpen={mergeAsk?.step === "how"}
          onClose={() => setMergeAsk(null)}
          title={`Open ${mergeAsk?.id ?? ""}`}
          message={`The cart already has ${cartItems.length} line${cartItems.length === 1 ? "" : "s"}. Replace them with ${mergeAsk?.id ?? "this order"}, or merge its lines into the cart?`}
          choices={[
            {
              label: "Replace",
              onChoose: () => {
                const id = mergeAsk!.id;
                setMergeAsk(null);
                openInCart(id, true).catch((e) => toast.error(e?.message || "Failed to add items to cart"));
              },
            },
            {
              label: "Merge",
              primary: true,
              disabledReason: mergeAsk ? mergeRefusal(mergeAsk.id, mergeAsk.data) : null,
              onChoose: () => {
                if (!mergeAsk) return;
                if (canCombine(cartItems, heldItemsToCart(mergeAsk.data.items))) setMergeAsk({ ...mergeAsk, step: "combine" });
                else finishMerge(false);
              },
            },
          ]}
        />

        <ChoiceDialog
          isOpen={mergeAsk?.step === "combine"}
          onClose={() => setMergeAsk(null)}
          title="Same items in both"
          message={`Some items in ${mergeAsk?.id ?? "this order"} are already in the cart. Keep them as separate lines, or add their quantities to the cart's lines (at the cart's prices)?`}
          choices={[
            { label: "Keep separate lines", onChoose: () => finishMerge(false) },
            { label: "Combine quantities", primary: true, onChoose: () => finishMerge(true) },
          ]}
        />

        <ConfirmDialog
          isOpen={!!withdrawTarget}
          onClose={() => setWithdrawTarget(null)}
          onConfirm={() => void handleWithdrawApproval()}
          title="Withdraw price approval"
          message={`Withdraw the price approval on held order ${withdrawTarget}? It will need approval again before it can be checked out.`}
          confirmText={isWithdrawingApproval ? "Withdrawing..." : "Withdraw"}
          cancelText="Cancel"
          confirmButtonClass="bg-red-600 hover:bg-red-700 text-white"
        />

        {showDraftPaymentDialog && (
          <PaymentDialog
            isOpen={showDraftPaymentDialog}
            backLabel="Back to list"
            onClose={(paymentCompleted) => {
              setShowDraftPaymentDialog(false);
              if (paymentCompleted) {
                refetch();
                void refetchHeldOrders();
              }
            }}
            cartItems={cartItems}
            appliedCoupons={[]}
            selectedCustomer={cartCustomer}
            onCompletePayment={() => {
              setShowDraftPaymentDialog(false);
              refetch();
              void refetchHeldOrders();
            }}
            onHoldOrder={() => setShowDraftPaymentDialog(false)}
            isMobile={false}
            isFullPage={false}
          />
        )}
      </div>
    </div>
  );
}

import { ChevronRight, CreditCard } from "lucide-react";
import { formatCurrencyWithSymbol } from "../../utils/currency";

/** One payment as get_invoice_details sends it (payment_rows.payment_details). */
export interface PaymentDetail {
  source: "till" | "payment_entry";
  mode_of_payment?: string | null;
  paid_to?: string | null;
  amount: number;
  reference_no?: string | null;
  reference_date?: string | null;
  posting_date?: string | null;
  clearance_date?: string | null;
  bank_account?: string | null;
  phone_number?: string | null;
  payment_entry?: string | null;
  remarks?: string | null;
  /** The till's Cash row when change was given: what was handed over, and what went back. */
  tendered?: number;
  change?: number;
}

interface Props {
  payments: PaymentDetail[] | null | undefined;
  /** Shown when there are no payment rows at all (an unpaid credit sale). */
  modeLabel?: string;
  currency: string;
  postingDate?: string;
  paidAmount: number;
  outstanding: number;
  dueDate?: string | null;
  status: string;
  statusClass: string;
}

const label = "text-orange-700 dark:text-orange-300";
// Mode | Reference | Date | Amount | chevron from sm up; on a phone each payment stacks in two lines.
const GRID = "grid-cols-[1fr_1fr_6rem_7rem_1rem]";
const GRID_SM = "sm:grid-cols-[1fr_1fr_6rem_7rem_1rem]";
const value = "text-orange-900 dark:text-orange-100";

/** The invoice's payments: one row each, opening onto everything that identifies it. */
export function PaymentDetails({ payments, modeLabel, currency, postingDate, paidAmount, outstanding, dueDate, status, statusClass }: Props) {
  const rows = payments ?? [];
  return (
    <div className="px-6 py-4 bg-orange-50 dark:bg-orange-900/20 border-t border-gray-200 dark:border-gray-600">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mb-3 text-sm">
        <div className="flex items-center space-x-2 mr-auto">
          <CreditCard className="w-5 h-5 text-orange-600 dark:text-orange-400" />
          <h4 className="font-semibold text-orange-900 dark:text-orange-100">Payment Details</h4>
        </div>
        <span className={`px-2 py-1 rounded-full text-xs font-medium ${statusClass}`}>{status}</span>
        <span><span className={label}>Paid </span><span className={`${value} font-semibold`}>{formatCurrencyWithSymbol(paidAmount, currency)}</span></span>
        {outstanding > 0 && (
          <span><span className="text-red-700 dark:text-red-300">Outstanding </span><span className="text-red-900 dark:text-red-100 font-semibold">{formatCurrencyWithSymbol(outstanding, currency)}</span></span>
        )}
        {outstanding > 0 && dueDate && (
          <span><span className={label}>Due </span><span className={value}>{dueDate}</span></span>
        )}
      </div>

      {rows.length === 0 ? (
        <p className={`text-sm ${value}`}>{modeLabel && modeLabel !== "-" ? modeLabel : "No payments recorded."}</p>
      ) : (
        <div className="text-sm">
          <div className={`hidden sm:grid ${GRID} gap-2 px-2 pb-1 text-xs font-medium ${label}`}>
            <span>Mode</span><span>Reference</span><span>Date</span><span className="text-right">Amount</span><span />
          </div>
          {rows.map((p, i) => {
            const facts = detailFacts(p, (n) => formatCurrencyWithSymbol(n, currency));
            const key = p.payment_entry || `till-${i}`;
            const cells = (
              <>
                <span className={`${value} font-medium truncate`}>{p.mode_of_payment || p.paid_to || "—"}</span>
                <span className={`${value} text-right sm:text-left font-semibold sm:font-normal tabular-nums sm:order-none order-2`}>
                  <span className="sm:hidden">{formatCurrencyWithSymbol(p.amount, currency)}</span>
                  <span className="hidden sm:inline truncate" title={p.reference_no || undefined}>{p.reference_no || "—"}</span>
                </span>
                <span className={`${value} text-xs sm:text-sm tabular-nums order-3 sm:order-none truncate`} title={p.reference_no || undefined}>
                  <span className="sm:hidden">{p.reference_no ? `${p.reference_no} · ` : ""}</span>
                  {p.reference_date || p.posting_date || postingDate || "—"}
                </span>
                <span className={`${value} hidden sm:block text-right font-semibold tabular-nums`}>{formatCurrencyWithSymbol(p.amount, currency)}</span>
              </>
            );
            const row = `grid grid-cols-[1fr_auto] ${GRID_SM} gap-x-2 gap-y-0.5 items-center px-2 py-1.5`;
            if (!facts.length) {
              return <div key={key} className={`${row} border-t border-orange-200/60 dark:border-orange-800/40`}>{cells}<span className="hidden sm:block" /></div>;
            }
            return (
              <details key={key} className="group border-t border-orange-200/60 dark:border-orange-800/40">
                <summary className={`${row} list-none [&::-webkit-details-marker]:hidden cursor-pointer hover:bg-orange-100/60 dark:hover:bg-orange-900/30`}>
                  {cells}
                  <ChevronRight aria-hidden className="hidden sm:block w-4 h-4 text-orange-600 dark:text-orange-400 transition-transform group-open:rotate-90" />
                </summary>
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 px-2 pb-2 pl-4 text-xs">
                  {facts.map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className={label}>{k}</dt>
                      <dd className={`${value} break-words whitespace-pre-line`}>{v}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** What else identifies a payment, beyond its row: only what is actually recorded. */
export function detailFacts(p: PaymentDetail, money: (n: number) => string = String): [string, string][] {
  const facts: [string, string | null | undefined][] = [
    ["Tendered", p.change ? money(p.tendered ?? 0) : null],
    ["Change", p.change ? money(p.change) : null],
    ["Payment Entry", p.payment_entry],
    ["Phone", p.phone_number],
    ["Cheque/Ref. date", p.reference_date],
    ["Received", p.source === "payment_entry" ? p.posting_date : null],
    ["Cleared", p.clearance_date],
    ["Bank account", p.bank_account],
    ["Paid to", p.mode_of_payment ? null : p.paid_to],
    ["Remarks", p.remarks],
  ];
  return facts.filter((f): f is [string, string] => !!f[1]);
}

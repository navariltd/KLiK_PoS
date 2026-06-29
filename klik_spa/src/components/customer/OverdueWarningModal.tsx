"use client"

interface OverdueInvoice {
  name: string
  due_date: string
  grand_total: number
  outstanding_amount: number
  currency: string
  customer_name: string
}

interface OverdueWarningModalProps {
  invoices: OverdueInvoice[]
  customerName: string
  onClose: () => void
}

const fmtDate = (d: string) =>
  d ? new Date(d).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—"

const fmtAmt = (n: number, currency: string) =>
  `${currency} ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function pad(str: string, len: number) {
  return str + " ".repeat(Math.max(0, len - str.length))
}

export default function OverdueWarningModal({ invoices, customerName, onClose }: OverdueWarningModalProps) {
  const currency = invoices[0]?.currency ?? ""
  const totalOutstanding = invoices.reduce((sum, inv) => sum + inv.outstanding_amount, 0)

  const copyReminder = () => {
    const sep = "-".repeat(72)
    const lines = [
      `Dear ${customerName},`,
      "Please note that the following invoices are overdue:",
      pad("Invoice", 22) + pad("Due Date", 16) + pad("Amount", 20) + "Outstanding",
      sep,
      ...invoices.map((inv) =>
        pad(inv.name, 22) +
        pad(fmtDate(inv.due_date), 16) +
        pad(fmtAmt(inv.grand_total, inv.currency), 20) +
        fmtAmt(inv.outstanding_amount, inv.currency)
      ),
      sep,
      `Total Outstanding: ${fmtAmt(totalOutstanding, currency)}`,
      "",
      "Kindly arrange for payment at your earliest convenience.",
      "Thank you.",
    ]
    const text = lines.join("\n")

    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).catch(() => fallbackCopy(text))
    } else {
      fallbackCopy(text)
    }
  }

  const fallbackCopy = (text: string) => {
    const el = document.createElement("textarea")
    el.value = text
    el.style.cssText = "position:fixed;opacity:0"
    document.body.appendChild(el)
    el.focus()
    el.select()
    try { document.execCommand("copy") } catch (_) {}
    document.body.removeChild(el)
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[250] p-4 animate-in fade-in duration-200">
      <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col animate-in slide-in-from-bottom-4 duration-300 border-l-4 border-red-500">

        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-red-500 text-lg font-bold">⚠</span>
              <h2 className="text-base font-bold text-gray-900 dark:text-white">
                Overdue Invoices — {customerName}
              </h2>
            </div>
            <p className="text-xs text-red-600 dark:text-red-400 font-medium">
              {invoices.length} overdue invoice{invoices.length !== 1 ? "s" : ""} found
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Table */}
        <div className="overflow-y-auto max-h-[55vh]">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-50 dark:bg-gray-700/80 border-b border-gray-200 dark:border-gray-600">
              <tr>
                <th className="text-left px-4 py-2.5 font-semibold text-gray-600 dark:text-gray-400 text-xs uppercase">Invoice</th>
                <th className="text-left px-4 py-2.5 font-semibold text-gray-600 dark:text-gray-400 text-xs uppercase">Due Date</th>
                <th className="text-right px-4 py-2.5 font-semibold text-gray-600 dark:text-gray-400 text-xs uppercase">Amount</th>
                <th className="text-right px-4 py-2.5 font-semibold text-gray-600 dark:text-gray-400 text-xs uppercase">Outstanding</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700/60">
              {invoices.map((inv) => (
                <tr key={inv.name} className="hover:bg-gray-50 dark:hover:bg-gray-700/20 transition-colors">
                  <td className="px-4 py-2.5">
                    <a
                      href={`/app/sales-invoice/${encodeURIComponent(inv.name)}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-beveren-600 dark:text-beveren-400 font-mono text-xs hover:underline"
                    >
                      {inv.name}
                    </a>
                  </td>
                  <td className="px-4 py-2.5 text-red-600 dark:text-red-400 font-medium text-xs">
                    {fmtDate(inv.due_date)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-700 dark:text-gray-300">
                    {fmtAmt(inv.grand_total, inv.currency)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-bold text-red-600 dark:text-red-400">
                    {fmtAmt(inv.outstanding_amount, inv.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot className="border-t-2 border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50">
              <tr>
                <td colSpan={3} className="px-4 py-2.5 text-right font-semibold text-gray-700 dark:text-gray-300 text-sm">
                  Total Outstanding
                </td>
                <td className="px-4 py-2.5 text-right font-bold text-red-600 dark:text-red-400 text-sm">
                  {fmtAmt(totalOutstanding, currency)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex gap-3">
          <button
            onClick={copyReminder}
            className="flex-1 py-2.5 bg-beveren-600 hover:bg-beveren-700 text-white font-semibold rounded-lg transition-all active:scale-[0.98] text-sm"
          >
            Copy Reminder
          </button>
          <button
            onClick={onClose}
            className="flex-1 py-2.5 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 font-semibold rounded-lg transition-all active:scale-[0.98] text-sm"
          >
            Close
          </button>
        </div>

      </div>
    </div>
  )
}

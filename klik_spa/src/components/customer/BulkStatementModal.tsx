import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, Loader2, Mail, X } from "lucide-react";
import { toast } from "react-toastify";
import {
  emailBulkStatements,
  getStatementTemplates,
  previewBulkStatements,
  type BulkStatementPreview,
  type StatementTemplate,
} from "../../services/statementOfAccounts";

interface BulkStatementModalProps {
  company: string;
  onClose: () => void;
}

const today = () => new Date().toISOString().slice(0, 10);

// Same key format as StatementOfAccountsModal: the per-company template choice is a shared
// preference, not a per-flow one, so a cashier who picked a template on one screen finds it
// already selected on the other.
const TEMPLATE_CACHE_KEY = (company: string) => `klik_pos_statement_template_${company}`;

export default function BulkStatementModal({ company, onClose }: BulkStatementModalProps) {
  const [templates, setTemplates] = useState<StatementTemplate[]>([]);
  const [template, setTemplate] = useState("");
  const [asOfDate, setAsOfDate] = useState(today);
  const [isLoadingTemplates, setIsLoadingTemplates] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [preview, setPreview] = useState<BulkStatementPreview | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isSending, setIsSending] = useState(false);

  // Clearing `preview` on a filter change is necessary but not sufficient: a preview request for
  // D1 already in flight when the user switches to D2 can still land afterwards and call
  // setPreview, silently re-enabling Send while the visible filters read D2. The sequence counter
  // closes that gap — bumped whenever a filter changes AND when a new preview starts, with the
  // result applied only if the captured sequence is still current when the response arrives.
  const previewSequence = useRef(0);

  // Templates, once per company. Mirrors StatementOfAccountsModal's loading/error handling
  // rather than inventing a second pattern.
  useEffect(() => {
    let isCurrent = true;
    setIsLoadingTemplates(true);
    setError(null);
    getStatementTemplates(company)
      .then((rows) => {
        if (!isCurrent) return;
        setTemplates(rows);
        const remembered = window.localStorage.getItem(TEMPLATE_CACHE_KEY(company));
        const usable = rows.find((row) => row.name === remembered) || rows[0];
        setTemplate(usable?.name || "");
        setError(rows.length === 0 ? `No statement template is configured for ${company}.` : null);
      })
      .catch((err) => {
        if (!isCurrent) return;
        setTemplates([]);
        setTemplate("");
        setError(err instanceof Error ? err.message : "Failed to load statement templates");
      })
      .finally(() => {
        if (isCurrent) setIsLoadingTemplates(false);
      });

    return () => {
      isCurrent = false;
    };
  }, [company]);

  // The single most important behaviour in this modal: a preview describes one exact
  // (template, as_of_date) pair. Changing either invalidates it immediately, so Send can never
  // fire against a combination nobody reviewed.
  const handleDateChange = (value: string) => {
    // Bump first: any in-flight preview response is now stale and must not be allowed to write
    // over this clear when it lands.
    previewSequence.current += 1;
    setAsOfDate(value);
    setPreview(null);
  };

  const rememberTemplate = useCallback(
    (name: string) => {
      previewSequence.current += 1;
      setTemplate(name);
      setPreview(null);
      window.localStorage.setItem(TEMPLATE_CACHE_KEY(company), name);
    },
    [company]
  );

  const canPreview = Boolean(template) && !isLoadingTemplates && !isPreviewing;

  const handlePreview = async () => {
    const sequence = ++previewSequence.current;
    setIsPreviewing(true);
    setError(null);
    try {
      const result = await previewBulkStatements(company, template, asOfDate);
      // A filter change (or another Preview click) since this request started has already bumped
      // the sequence — this response describes a population nobody is looking at anymore.
      if (sequence !== previewSequence.current) return;
      setPreview(result);
    } catch (err) {
      if (sequence !== previewSequence.current) return;
      setPreview(null);
      setError(err instanceof Error ? err.message : "Failed to preview statements");
    } finally {
      if (sequence === previewSequence.current) setIsPreviewing(false);
    }
  };

  // Preview-then-send: Send only ever fires against a preview that is still on screen, and only
  // when that preview actually found someone to send to.
  const canSend = Boolean(preview) && preview!.will_send.length > 0 && !isSending;

  const handleSend = async () => {
    // Synchronous guard: `disabled` on the button is not enough to stop a second click that lands
    // before React re-renders it disabled, and a double-send here means every customer in the
    // preview gets two statements.
    if (isSending) return;
    if (!preview) return;
    setIsSending(true);
    try {
      const result = await emailBulkStatements(company, template, asOfDate);
      toast.success(`Statements queued for ${result.queued} customers`);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to send statements");
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg bg-white shadow-xl dark:bg-gray-900">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4 dark:border-gray-700">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-beveren-100 text-beveren-700 dark:bg-beveren-900 dark:text-beveren-300">
              <FileText size={20} />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Send Statements</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400">{company}</p>
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

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {error && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
              {error}
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">As of</label>
              <input
                type="date"
                value={asOfDate}
                onChange={(event) => handleDateChange(event.target.value)}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
              />
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">Template</label>
              <select
                value={template}
                onChange={(event) => rememberTemplate(event.target.value)}
                disabled={isLoadingTemplates || templates.length === 0}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 disabled:bg-gray-100 dark:border-gray-600 dark:bg-gray-800 dark:text-white dark:disabled:bg-gray-800/60"
              >
                {templates.map((row) => (
                  <option key={row.name} value={row.name}>
                    {row.name} ({row.report})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {preview ? (
            <div className="space-y-4">
              <p className="text-sm text-gray-500 dark:text-gray-400">
                {preview.total_customers} customer{preview.total_customers === 1 ? "" : "s"} considered.
              </p>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-gray-900 dark:text-white">
                  Will receive ({preview.will_send.length})
                </h3>
                {preview.will_send.length > 0 ? (
                  <ul className="max-h-40 divide-y divide-gray-200 overflow-y-auto rounded-lg border border-gray-200 dark:divide-gray-700 dark:border-gray-700">
                    {preview.will_send.map((row) => (
                      <li
                        key={row.customer}
                        className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                      >
                        <span className="text-gray-900 dark:text-white">{row.customer_name}</span>
                        <span className="truncate text-gray-500 dark:text-gray-400">{row.recipient}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-gray-500 dark:text-gray-400">
                    No customers will receive a statement.
                  </p>
                )}
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-gray-900 dark:text-white">
                  No email on file ({preview.no_email.length})
                </h3>
                {/* Rendered even when empty: its absence is informative, not ambiguous. */}
                {preview.no_email.length > 0 ? (
                  <ul className="max-h-40 divide-y divide-gray-200 overflow-y-auto rounded-lg border border-gray-200 dark:divide-gray-700 dark:border-gray-700">
                    {preview.no_email.map((row) => (
                      <li key={row.customer} className="px-3 py-2 text-sm text-gray-900 dark:text-white">
                        {row.customer_name}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-gray-500 dark:text-gray-400">
                    Every customer with transactions has an email on file.
                  </p>
                )}
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800">
                  <span className="block text-sm text-gray-500 dark:text-gray-400">No transactions</span>
                  <span className="text-base font-semibold text-gray-900 dark:text-white">
                    {preview.no_transactions}
                  </span>
                </div>
                <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800">
                  <span className="block text-sm text-gray-500 dark:text-gray-400">Not permitted</span>
                  <span className="text-base font-semibold text-gray-900 dark:text-white">
                    {preview.not_permitted}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex h-32 items-center justify-center rounded-lg border border-gray-200 text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
              {isPreviewing ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 size={18} className="animate-spin" />
                  Previewing...
                </span>
              ) : (
                "Run Preview to see who will receive a statement."
              )}
            </div>
          )}
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
            type="button"
            onClick={handlePreview}
            disabled={!canPreview}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            {isPreviewing ? <Loader2 size={16} className="animate-spin" /> : <FileText size={16} />}
            <span>Preview</span>
          </button>
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            className="inline-flex items-center gap-2 rounded-lg bg-beveren-600 px-4 py-2 text-sm font-medium text-white hover:bg-beveren-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {isSending ? <Loader2 size={16} className="animate-spin" /> : <Mail size={16} />}
            <span>Send statements{preview ? ` (${preview.will_send.length})` : ""}</span>
          </button>
        </div>
      </div>
    </div>
  );
}

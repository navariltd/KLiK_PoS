import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, FileText, Loader2, Mail, X } from "lucide-react";
import { toast } from "react-toastify";
import {
  downloadStatement,
  emailStatement,
  getStatementRecipient,
  getStatementTemplates,
  renderStatementHtml,
  type StatementTemplate,
} from "../../services/statementOfAccounts";

interface StatementOfAccountsModalProps {
  customer: string;
  customerName: string;
  company: string;
  onClose: () => void;
}

const today = () => new Date().toISOString().slice(0, 10);

const TEMPLATE_CACHE_KEY = (company: string) => `klik_pos_statement_template_${company}`;

export default function StatementOfAccountsModal({
  customer,
  customerName,
  company,
  onClose,
}: StatementOfAccountsModalProps) {
  const [templates, setTemplates] = useState<StatementTemplate[]>([]);
  const [template, setTemplate] = useState("");
  const [asOfDate, setAsOfDate] = useState(today);
  const [recipient, setRecipient] = useState("");
  const [cc, setCc] = useState("");
  const [bcc, setBcc] = useState("");
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [html, setHtml] = useState("");
  const [isLoadingTemplates, setIsLoadingTemplates] = useState(true);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isEmailing, setIsEmailing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Invariant 1: sequence-number the preview so a slow early render cannot overwrite a fast
  // later one. A plain debounce alone does not prevent that.
  const previewSequence = useRef(0);

  // Templates, once per company. Restores the last one used, like the desk dialog's sticky
  // per-user setting — localStorage rather than a server round-trip.
  useEffect(() => {
    let isCurrent = true;
    setIsLoadingTemplates(true);
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
        setError(err instanceof Error ? err.message : "Failed to load statement templates");
      })
      .finally(() => {
        if (isCurrent) setIsLoadingTemplates(false);
      });

    return () => {
      isCurrent = false;
    };
  }, [company]);

  // Invariants 3 and 4: exactly one fetch per customer, and the result is ALWAYS assigned —
  // including when it is empty, or switching customers would leave the previous address in
  // the To field and send the statement to the wrong person.
  useEffect(() => {
    let isCurrent = true;
    getStatementRecipient(customer)
      .then((address) => {
        if (isCurrent) setRecipient(address);
      })
      .catch(() => {
        if (isCurrent) setRecipient("");
      });

    return () => {
      isCurrent = false;
    };
  }, [customer]);

  // Debounced, sequence-numbered preview (invariant 1).
  useEffect(() => {
    if (!template || !company || !customer) {
      setHtml("");
      return;
    }

    const sequence = ++previewSequence.current;
    setIsPreviewing(true);
    const timer = window.setTimeout(() => {
      renderStatementHtml({ customer, company, template, as_of_date: asOfDate })
        .then((rendered) => {
          if (sequence !== previewSequence.current) return;
          setHtml(rendered);
          setError(null);
        })
        .catch((err) => {
          if (sequence !== previewSequence.current) return;
          setHtml("");
          setError(err instanceof Error ? err.message : "Failed to render the statement");
        })
        .finally(() => {
          if (sequence === previewSequence.current) setIsPreviewing(false);
        });
    }, 350);

    return () => window.clearTimeout(timer);
  }, [customer, company, template, asOfDate]);

  const rememberTemplate = useCallback(
    (name: string) => {
      setTemplate(name);
      window.localStorage.setItem(TEMPLATE_CACHE_KEY(company), name);
    },
    [company]
  );

  // Invariant 2: actions re-derive from current field values. They never reuse `html`, which
  // may still be mid-debounce and describe superseded filters.
  const statementArgs = useMemo(
    () => ({ customer, company, template, as_of_date: asOfDate }),
    [customer, company, template, asOfDate]
  );

  const canAct = Boolean(template) && !isLoadingTemplates;

  const handleDownload = async () => {
    setIsDownloading(true);
    try {
      await downloadStatement(statementArgs);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to download the statement");
    } finally {
      setIsDownloading(false);
    }
  };

  const handleEmail = async () => {
    if (!recipient.trim()) {
      toast.error("Enter an email address to send the statement to.");
      return;
    }
    setIsEmailing(true);
    try {
      await emailStatement({ ...statementArgs, recipient, cc, bcc });
      toast.success(`Statement sent to ${recipient}`);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to email the statement");
    } finally {
      setIsEmailing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-lg bg-white shadow-xl dark:bg-gray-900">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4 dark:border-gray-700">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-beveren-100 text-beveren-700 dark:bg-beveren-900 dark:text-beveren-300">
              <FileText size={20} />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Statement of Accounts</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400">{customerName}</p>
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
                onChange={(event) => setAsOfDate(event.target.value)}
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

          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">To</label>
            <input
              type="email"
              value={recipient}
              onChange={(event) => setRecipient(event.target.value)}
              placeholder="No email on file — type one to send"
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
            />
            {!showCcBcc && (
              <button
                type="button"
                onClick={() => setShowCcBcc(true)}
                className="mt-1 text-sm text-beveren-700 hover:underline dark:text-beveren-300"
              >
                + Add CC / BCC
              </button>
            )}
          </div>

          {showCcBcc && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">CC</label>
                <input
                  type="text"
                  value={cc}
                  onChange={(event) => setCc(event.target.value)}
                  className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">BCC</label>
                <input
                  type="text"
                  value={bcc}
                  onChange={(event) => setBcc(event.target.value)}
                  className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-beveren-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white"
                />
              </div>
            </div>
          )}

          <div className="relative min-h-[320px] overflow-hidden rounded-lg border border-gray-200 bg-white dark:border-gray-700">
            {isPreviewing && (
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70 dark:bg-gray-900/70">
                <span className="inline-flex items-center gap-2 text-gray-600 dark:text-gray-300">
                  <Loader2 size={18} className="animate-spin" />
                  Rendering preview...
                </span>
              </div>
            )}
            {html ? (
              <iframe srcDoc={html} title="Statement preview" className="h-[420px] w-full bg-white" />
            ) : (
              <div className="flex h-[320px] items-center justify-center text-sm text-gray-500 dark:text-gray-400">
                {isPreviewing ? "" : "No statement to preview."}
              </div>
            )}
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
            type="button"
            onClick={handleDownload}
            disabled={!canAct || isDownloading}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            {isDownloading ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
            <span>Download</span>
          </button>
          <button
            type="button"
            onClick={handleEmail}
            disabled={!canAct || isEmailing}
            className="inline-flex items-center gap-2 rounded-lg bg-beveren-600 px-4 py-2 text-sm font-medium text-white hover:bg-beveren-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {isEmailing ? <Loader2 size={16} className="animate-spin" /> : <Mail size={16} />}
            <span>Email</span>
          </button>
        </div>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { Loader2, X, History } from "lucide-react";
import { toast } from "react-toastify";

import { updateWalkinCustomerInfo, type WalkinInfoChangeLogEntry } from "../../services/salesInvoice";
import { extractErrorFromException } from "../../utils/errorExtraction";

interface EditWalkinInfoModalProps {
  isOpen: boolean;
  onClose: () => void;
  invoiceName: string;
  initialAlias: string;
  initialTaxId: string;
  initialChangeLog?: WalkinInfoChangeLogEntry[];
  onSaved: (updated: { alias: string; taxId: string; changeLog: WalkinInfoChangeLogEntry[] }) => void;
}

const FIELD_LABELS: Record<string, string> = {
  custom_customer_alias: "Customer Name",
  tax_id: "Tax ID",
};

export default function EditWalkinInfoModal({
  isOpen,
  onClose,
  invoiceName,
  initialAlias,
  initialTaxId,
  initialChangeLog = [],
  onSaved,
}: EditWalkinInfoModalProps) {
  const [alias, setAlias] = useState(initialAlias);
  const [taxId, setTaxId] = useState(initialTaxId);
  const [isSaving, setIsSaving] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setAlias(initialAlias || "");
    setTaxId(initialTaxId || "");
    setShowHistory(false);
  }, [isOpen, initialAlias, initialTaxId]);

  if (!isOpen) {
    return null;
  }

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const result = await updateWalkinCustomerInfo(invoiceName, {
        alias: alias.trim(),
        taxId: taxId.trim().toUpperCase(),
      });

      if (!result.changed) {
        toast.info("Nothing to update.");
        onClose();
        return;
      }

      toast.success("Customer info updated.");
      onSaved({
        alias: result.custom_customer_alias || "",
        taxId: result.tax_id || "",
        changeLog: result.change_log || [],
      });
      onClose();
    } catch (err) {
      toast.error(extractErrorFromException(err, "Failed to update customer info"));
    } finally {
      setIsSaving(false);
    }
  };

  const sortedLog = [...initialChangeLog].reverse();

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-gray-950/45 px-4">
      <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl ring-1 ring-gray-200 dark:bg-gray-900 dark:ring-gray-700">
        <div className="flex items-start justify-between border-b border-gray-100 px-6 py-5 dark:border-gray-800">
          <div>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Edit customer info</h2>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              Correct or add the walk-in customer's name and Tax ID for {invoiceName}.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4 px-6 py-5">
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-200">
              Customer Name
            </label>
            <input
              type="text"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="e.g. Jane Wanjiku"
              disabled={isSaving}
              className="w-full rounded-xl border border-gray-300 bg-white px-4 py-2.5 text-sm text-gray-900 outline-none transition-colors focus:border-beveren-500 focus:ring-2 focus:ring-beveren-200 dark:border-gray-700 dark:bg-gray-900 dark:text-white"
            />
          </div>

          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-200">
              Tax ID
            </label>
            <input
              type="text"
              value={taxId}
              onChange={(e) => setTaxId(e.target.value.toUpperCase())}
              placeholder="A123456789P"
              disabled={isSaving}
              className="w-full rounded-xl border border-gray-300 bg-white px-4 py-2.5 text-sm uppercase tracking-widest text-gray-900 outline-none transition-colors focus:border-beveren-500 focus:ring-2 focus:ring-beveren-200 dark:border-gray-700 dark:bg-gray-900 dark:text-white"
            />
          </div>

          {sortedLog.length > 0 && (
            <div>
              <button
                type="button"
                onClick={() => setShowHistory((prev) => !prev)}
                className="inline-flex items-center gap-2 text-sm font-medium text-beveren-600 hover:text-beveren-700"
              >
                <History className="h-4 w-4" />
                {showHistory ? "Hide edit history" : `View edit history (${sortedLog.length})`}
              </button>
              {showHistory && (
                <div className="mt-3 max-h-40 space-y-2 overflow-y-auto rounded-xl border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-950/40">
                  {sortedLog.map((entry, idx) => (
                    <div key={idx} className="text-xs text-gray-600 dark:text-gray-300">
                      <span className="font-medium text-gray-800 dark:text-gray-100">
                        {FIELD_LABELS[entry.field] || entry.field}
                      </span>
                      {": "}
                      <span className="line-through">{entry.old_value || "(blank)"}</span>
                      {" -> "}
                      <span className="font-medium">{entry.new_value || "(blank)"}</span>
                      <div className="text-gray-400 dark:text-gray-500">
                        by {entry.changed_by} on {new Date(entry.changed_on).toLocaleString()}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4 dark:border-gray-800">
          <button
            type="button"
            onClick={onClose}
            disabled={isSaving}
            className="rounded-xl border border-gray-300 px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={isSaving}
            className="inline-flex items-center gap-2 rounded-xl bg-beveren-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-beveren-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            <span>Save</span>
          </button>
        </div>
      </div>
    </div>
  );
}
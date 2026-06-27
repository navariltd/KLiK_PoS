import { useState } from "react";
import { X } from "lucide-react";
import type { WalkinDetails } from "../../stores/cartStore";
import { useExtraFields } from "../../hooks/useExtraFields";

interface Props {
  isWalkin: boolean;
  initial: WalkinDetails;
  masterDisplay: WalkinDetails;
  extraFields: Record<string, string>;
  onClose: () => void;
  onSave: (d: WalkinDetails & { extraFields: Record<string, string> }) => void;
}

export default function WalkinInfoModal({ isWalkin, initial, masterDisplay, extraFields, onClose, onSave }: Props) {
  const seed = isWalkin ? initial : masterDisplay;
  const [name, setName] = useState(seed.name);
  const [taxId, setTaxId] = useState(seed.taxId);
  const [phone, setPhone] = useState(seed.phone);
  const { fields } = useExtraFields();
  const [extra, setExtra] = useState<Record<string, string>>(extraFields || {});

  const base =
    "w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-beveren-500";
  const ro = "opacity-60 cursor-not-allowed bg-gray-100 dark:bg-gray-800";

  const setField = (fn: string, v: string) => setExtra((p) => ({ ...p, [fn]: v }));
  const missingRequired = fields.some((f) => f.reqd && !(extra[f.fieldname] || "").trim());

  const renderControl = (f: ReturnType<typeof useExtraFields>["fields"][number]) => {
    const val = extra[f.fieldname] ?? "";
    if (f.fieldtype === "Select") {
      return (
        <select className={base} value={val} onChange={(e) => setField(f.fieldname, e.target.value)}>
          <option value="">—</option>
          {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    }
    if (f.fieldtype === "Check") {
      return (
        <input type="checkbox" checked={val === "1"} onChange={(e) => setField(f.fieldname, e.target.checked ? "1" : "0")} />
      );
    }
    const inputType = f.fieldtype === "Int" || f.fieldtype === "Float" ? "number"
      : f.fieldtype === "Date" ? "date" : "text";
    return (
      <input className={base} type={inputType} value={val} onChange={(e) => setField(f.fieldname, e.target.value)} />
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className={`w-full ${fields.length ? "max-w-2xl" : "max-w-sm"} rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl`}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white">Additional Info</h3>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded">
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>
        {!isWalkin && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
            Name, Tax ID and Phone come from the customer record and are read-only.
          </p>
        )}
        <div className={`grid gap-x-6 gap-y-3 ${fields.length ? "grid-cols-2" : "grid-cols-1"}`}>
          <div className="space-y-3">
            <div>
              <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Name</label>
              <input className={`${base} ${isWalkin ? "" : ro}`} value={name} readOnly={!isWalkin}
                onChange={(e) => setName(e.target.value)} placeholder="Customer name for this sale" />
            </div>
            <div>
              <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Tax ID</label>
              <input className={`${base} uppercase tracking-widest ${isWalkin ? "" : ro}`} value={taxId} readOnly={!isWalkin}
                onChange={(e) => setTaxId(e.target.value.toUpperCase())} placeholder="A123456789P" />
            </div>
            <div>
              <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Phone</label>
              <input className={`${base} ${isWalkin ? "" : ro}`} value={phone} readOnly={!isWalkin} type="tel"
                onChange={(e) => setPhone(e.target.value)} placeholder="Phone for this sale" />
            </div>
          </div>
          {fields.length > 0 && (
            <div className="space-y-3">
              {fields.map((f) => (
                <div key={f.fieldname}>
                  <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">
                    {f.label}{f.reqd && <span className="text-red-500"> *</span>}
                  </label>
                  {renderControl(f)}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200">Cancel</button>
          <button
            disabled={missingRequired}
            onClick={() => onSave({ name: name.trim(), taxId: taxId.trim(), phone: phone.trim(), extraFields: extra })}
            className="px-3 py-2 text-sm rounded-lg bg-beveren-600 text-white hover:bg-beveren-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

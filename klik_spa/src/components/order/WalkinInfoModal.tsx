import { useState } from "react";
import { X } from "lucide-react";
import type { WalkinDetails } from "../../stores/cartStore";

interface Props {
  initial: WalkinDetails;
  onClose: () => void;
  onSave: (d: WalkinDetails) => void;
}

export default function WalkinInfoModal({ initial, onClose, onSave }: Props) {
  const [name, setName] = useState(initial.name);
  const [taxId, setTaxId] = useState(initial.taxId);
  const [phone, setPhone] = useState(initial.phone);

  const inputCls =
    "w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-beveren-500";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white">Walk-in Customer Info</h3>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded">
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Name (optional)</label>
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Customer name for this sale" />
          </div>
          <div>
            <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Tax ID (optional)</label>
            <input className={`${inputCls} uppercase tracking-widest`} value={taxId} onChange={(e) => setTaxId(e.target.value.toUpperCase())} placeholder="A123456789P" />
          </div>
          <div>
            <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Phone (optional)</label>
            <input className={inputCls} type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Phone for this sale" />
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200">Cancel</button>
          <button
            onClick={() => onSave({ name: name.trim(), taxId: taxId.trim(), phone: phone.trim() })}
            className="px-3 py-2 text-sm rounded-lg bg-beveren-600 text-white hover:bg-beveren-700"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

import { useState } from "react";
import { X } from "lucide-react";
import type { WalkinDetails } from "../../stores/cartStore";

interface Props {
  isWalkin: boolean;
  initial: WalkinDetails;            // editable seed for walk-in
  masterDisplay: WalkinDetails;      // read-only values for non-walk-in
  onClose: () => void;
  onSave: (d: WalkinDetails) => void;
}

export default function WalkinInfoModal({ isWalkin, initial, masterDisplay, onClose, onSave }: Props) {
  const seed = isWalkin ? initial : masterDisplay;
  const [name, setName] = useState(seed.name);
  const [taxId, setTaxId] = useState(seed.taxId);
  const [phone, setPhone] = useState(seed.phone);

  const base =
    "w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-beveren-500";
  const ro = "opacity-60 cursor-not-allowed bg-gray-100 dark:bg-gray-800";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
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
        <div className="space-y-3">
          <div>
            <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Name</label>
            <input
              className={`${base} ${isWalkin ? "" : ro}`}
              value={name}
              readOnly={!isWalkin}
              onChange={(e) => setName(e.target.value)}
              placeholder="Customer name for this sale"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Tax ID</label>
            <input
              className={`${base} uppercase tracking-widest ${isWalkin ? "" : ro}`}
              value={taxId}
              readOnly={!isWalkin}
              onChange={(e) => setTaxId(e.target.value.toUpperCase())}
              placeholder="A123456789P"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-700 dark:text-gray-300 mb-1">Phone</label>
            <input
              className={`${base} ${isWalkin ? "" : ro}`}
              value={phone}
              readOnly={!isWalkin}
              type="tel"
              onChange={(e) => setPhone(e.target.value)}
              placeholder="Phone for this sale"
            />
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200">Cancel</button>
          <button
            onClick={() =>
              onSave({
                name: name.trim(),
                taxId: taxId.trim(),
                phone: phone.trim(),
              })
            }
            className="px-3 py-2 text-sm rounded-lg bg-beveren-600 text-white hover:bg-beveren-700"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

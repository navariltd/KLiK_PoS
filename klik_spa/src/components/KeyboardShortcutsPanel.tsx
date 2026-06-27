"use client";

import { useEffect } from "react";
import { X } from "lucide-react";

interface KeyboardShortcutsPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

interface ShortcutGroup {
  label: string;
  shortcuts: { keys: string[]; description: string; separator?: string }[];
}

const GROUPS: ShortcutGroup[] = [
  {
    label: "Search",
    shortcuts: [
      { keys: ["F3"], description: "Focus search box" },
      { keys: ["Backspace"], description: "Focus search (from anywhere)" },
      { keys: ["↓"], description: "Move focus into product list" },
      { keys: ["Enter"], description: "Add to cart when 1 result shown" },
    ],
  },
  {
    label: "Product List",
    shortcuts: [
      { keys: ["↑", "↓"], description: "Previous / next item", separator: "/" },
      { keys: ["+", "−"], description: "Increase / decrease qty", separator: "/" },
    ],
  },
  {
    label: "Order",
    shortcuts: [
      { keys: ["F2"], description: "Open customer dropdown" },
      { keys: ["F10"], description: "Checkout / submit payment" },
      { keys: ["Shift", "F10"], description: "Hold order" },
    ],
  },
  {
    label: "General",
    shortcuts: [
      { keys: ["?"], description: "Show / hide this reference" },
    ],
  },
];

function Key({ label }: { label: string }) {
  const wide = label.length > 3;
  return (
    <span
      className={`inline-flex items-center justify-center font-mono text-xs font-semibold text-gray-700 dark:text-gray-200
        bg-white dark:bg-gray-700
        border border-gray-300 dark:border-gray-500
        rounded
        shadow-[0_2px_0_0_rgb(0,0,0,0.18)] dark:shadow-[0_2px_0_0_rgb(0,0,0,0.45)]
        select-none
        ${wide ? "px-2 py-1" : "w-7 h-7"}
      `}
    >
      {label}
    </span>
  );
}

function ShortcutRow({ keys, description, separator = "+" }: { keys: string[]; description: string; separator?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <span className="text-xs text-gray-500 dark:text-gray-400 leading-snug">{description}</span>
      <div className="flex items-center gap-1 flex-shrink-0">
        {keys.map((k, i) => (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <span className="text-gray-400 dark:text-gray-500 text-[10px] font-medium">{separator}</span>}
            <Key label={k} />
          </span>
        ))}
      </div>
    </div>
  );
}

export default function KeyboardShortcutsPanel({ isOpen, onClose }: KeyboardShortcutsPanelProps) {
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === "?") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center"
      onClick={onClose}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[2px]" />

      {/* Panel */}
      <div
        className="relative z-10 w-[420px] max-h-[90vh] overflow-y-auto bg-white dark:bg-gray-900 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-2.5">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="text-gray-500 dark:text-gray-400">
              <rect x="2" y="6" width="20" height="13" rx="2"/>
              <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h.01M12 14h.01M16 14h.01M6 14h.01M18 14h.01"/>
            </svg>
            <span className="text-sm font-semibold text-gray-900 dark:text-white">Keyboard Shortcuts</span>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            <X size={14} />
          </button>
        </div>

        {/* Groups */}
        <div className="px-5 py-4 space-y-5">
          {GROUPS.map((group) => (
            <div key={group.label}>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 mb-2">
                {group.label}
              </p>
              <div className="divide-y divide-gray-50 dark:divide-gray-800">
                {group.shortcuts.map((s, i) => (
                  <ShortcutRow key={i} keys={s.keys} description={s.description} />
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer hint */}
        <div className="px-5 py-3 border-t border-gray-100 dark:border-gray-800 flex items-center justify-between">
          <span className="text-[11px] text-gray-400 dark:text-gray-500">Press <Key label="?" /> or <Key label="Esc" /> to close</span>
        </div>
      </div>
    </div>
  );
}

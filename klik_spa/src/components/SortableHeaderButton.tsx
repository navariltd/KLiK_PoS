import { ChevronDown, ChevronUp, ChevronsUpDown } from "lucide-react";
import type { SortDirection } from "../hooks/useTableSort";

interface SortableHeaderButtonProps {
  label: string;
  sortKey: string;
  activeKey: string | null;
  direction: SortDirection;
  onSort: (key: string) => void;
}

export default function SortableHeaderButton({
  label,
  sortKey,
  activeKey,
  direction,
  onSort,
}: SortableHeaderButtonProps) {
  const isActive = activeKey === sortKey;
  const Icon = isActive ? (direction === "asc" ? ChevronUp : ChevronDown) : ChevronsUpDown;

  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      className="inline-flex items-center gap-1 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
    >
      <span>{label}</span>
      <Icon className={`w-3.5 h-3.5 ${isActive ? "text-beveren-600 dark:text-beveren-400" : "text-gray-400"}`} />
    </button>
  );
}

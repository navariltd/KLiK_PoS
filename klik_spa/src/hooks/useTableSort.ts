import { useState } from "react";

export type SortDirection = "asc" | "desc";

export type SortAccessor<T> = (item: T) => string | number | null | undefined;

export function useTableSort<T>(data: T[], accessors: Record<string, SortAccessor<T>>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

  let sortedData = data;
  const getValue = sortKey ? accessors[sortKey] : null;
  if (getValue) {
    sortedData = [...data].sort((a, b) => {
      const av = getValue(a);
      const bv = getValue(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;

      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });

      return sortDirection === "asc" ? cmp : -cmp;
    });
  }

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDirection("asc");
    }
  };

  return { sortedData, sortKey, sortDirection, toggleSort };
}

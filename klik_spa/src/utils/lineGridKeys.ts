export type GridCol = "item" | "qty" | "uom" | "rate" | "disc";
export const GRID_COLS: GridCol[] = ["item", "qty", "uom", "rate", "disc"];
export interface GridKeyCtx {
  key: string; shift: boolean; ctrl: boolean; alt: boolean;
  col: GridCol; row: number;          // row index in display order; entryRow = index of the empty entry row
  rowCount: number; entryRow: number;
  dropdownOpen: boolean; rowEmpty: boolean;
  editable: { rate: boolean; disc: boolean; uom?: boolean };   // allow_rate_change / allow_discount_change; uom: this line sells in more than one unit
}
export type GridAction =
  | { type: "focus"; row: number; col: GridCol }
  | { type: "commit"; then: { row: number; col: GridCol } }   // commit edit, then focus
  | { type: "pickHighlighted" } | { type: "moveHighlight"; delta: 1 | -1 }
  | { type: "closeDropdown" } | { type: "deleteRow"; row: number; then: { row: number; col: GridCol } }   // `then` is a POST-deletion display index
  | { type: "submitEntry" }          // Enter in the entry row's Item cell: exact lookup, else pick highlighted
  | { type: "openDescription"; row: number }
  | { type: "none" };                // let the browser handle it

const NONE: GridAction = { type: "none" };

export function gridKeyAction(c: GridKeyCtx): GridAction {
  const { key, col, row, entryRow } = c;
  const isEntry = row === entryRow;
  const cols = GRID_COLS.filter((x) => x === "item" || x === "qty" || !!c.editable[x as "uom" | "rate" | "disc"]) as [GridCol, ...GridCol[]];
  const i = cols.indexOf(col);

  if (c.dropdownOpen) {
    if ((key === "Enter" || key === "Tab") && !c.shift) return { type: "pickHighlighted" };
    if (key === "ArrowDown") return { type: "moveHighlight", delta: 1 };
    if (key === "ArrowUp") return { type: "moveHighlight", delta: -1 };
    if (key === "Escape") return { type: "closeDropdown" };
  }

  // `then` is a post-deletion index: the following line shifts into `row`; after the last line, the entry row
  // (whose index drops by one if it sat below the deleted row) takes focus
  const lastLine = row === (entryRow === c.rowCount - 1 ? c.rowCount - 2 : c.rowCount - 1);
  const afterDelete = (): { row: number; col: GridCol } =>
    lastLine ? { row: entryRow > row ? entryRow - 1 : entryRow, col: "item" } : { row, col };

  if (c.alt && key.toLowerCase() === "d") return isEntry ? NONE : { type: "openDescription", row };
  if (key === "Delete" && c.shift) return isEntry ? NONE : { type: "deleteRow", row, then: afterDelete() };
  if (key === "Escape") return !isEntry && c.rowEmpty ? { type: "deleteRow", row, then: afterDelete() } : NONE;

  if (key === "ArrowUp" || key === "ArrowDown") {
    const to = Math.min(c.rowCount - 1, Math.max(0, row + (key === "ArrowDown" ? 1 : -1)));
    if (to === row) return NONE;
    return col === "item" ? { type: "focus", row: to, col } : { type: "commit", then: { row: to, col } };
  }

  if (key === "Tab" && c.shift) {
    if (i > 0) return { type: "focus", row, col: cols[i - 1]! };
    if (row === 0) return NONE;
    return { type: "focus", row: row - 1, col: row - 1 === entryRow ? "item" : cols[cols.length - 1]! };
  }

  if (key === "Enter" || key === "Tab") {
    if (col === "item") {
      if (!isEntry) return { type: "focus", row, col: "qty" };
      return key === "Enter" ? { type: "submitEntry" } : NONE;
    }
    const then = i < cols.length - 1 ? { row, col: cols[i + 1]! } : { row: entryRow, col: "item" as GridCol };
    return { type: "commit", then };
  }
  return NONE;
}

/** A committed qty cell: a typed 0 removes the line, as in the card view; a new positive qty sets it. */
export function qtyCommit(n: number | undefined, current: number): number | "remove" | null {
  if (n === undefined || !Number.isFinite(n) || n < 0) return null;
  if (n === 0) return "remove";
  return n === current ? null : n;
}

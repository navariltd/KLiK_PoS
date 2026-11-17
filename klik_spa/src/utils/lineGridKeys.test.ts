import { describe, it, expect } from "vitest";
import { gridKeyAction, qtyCommit, type GridKeyCtx } from "./lineGridKeys";

// 3 lines (0..2) + entry row at 3 (Bottom)
const base: GridKeyCtx = {
  key: "", shift: false, ctrl: false, alt: false, col: "qty", row: 1,
  rowCount: 4, entryRow: 3, dropdownOpen: false, rowEmpty: false,
  editable: { rate: true, disc: true },
};
const act = (o: Partial<GridKeyCtx>) => gridKeyAction({ ...base, ...o });
const both = { rate: false, disc: false };

describe("gridKeyAction", () => {
  it("Enter/Tab with dropdown open picks highlighted", () => {
    expect(act({ col: "item", row: 3, dropdownOpen: true, key: "Enter" })).toEqual({ type: "pickHighlighted" });
    expect(act({ col: "item", row: 3, dropdownOpen: true, key: "Tab" })).toEqual({ type: "pickHighlighted" });
  });
  it("arrows with dropdown open move highlight", () => {
    expect(act({ col: "item", row: 3, dropdownOpen: true, key: "ArrowDown" })).toEqual({ type: "moveHighlight", delta: 1 });
    expect(act({ col: "item", row: 3, dropdownOpen: true, key: "ArrowUp" })).toEqual({ type: "moveHighlight", delta: -1 });
  });
  it("Enter on entry item, closed, submits; Tab is browser default", () => {
    expect(act({ col: "item", row: 3, key: "Enter" })).toEqual({ type: "submitEntry" });
    expect(act({ col: "item", row: 3, key: "Tab" })).toEqual({ type: "none" });
  });
  it("existing line item: Enter/Tab focus qty", () => {
    expect(act({ col: "item", key: "Enter" })).toEqual({ type: "focus", row: 1, col: "qty" });
    expect(act({ col: "item", key: "Tab" })).toEqual({ type: "focus", row: 1, col: "qty" });
  });
  it("a line with more than one unit stops on its UOM between qty and rate", () => {
    const uom = { rate: true, disc: true, uom: true };
    expect(act({ key: "Tab", editable: uom })).toEqual({ type: "commit", then: { row: 1, col: "uom" } });
    expect(act({ key: "Enter", col: "uom", editable: uom })).toEqual({ type: "commit", then: { row: 1, col: "rate" } });
    expect(act({ key: "Tab", shift: true, col: "rate", editable: uom })).toEqual({ type: "focus", row: 1, col: "uom" });
    expect(act({ key: "ArrowDown", col: "uom", editable: uom })).toEqual({ type: "commit", then: { row: 2, col: "uom" } });
  });
  it("qty -> rate, skipping non-editable cells", () => {
    expect(act({ key: "Enter" })).toEqual({ type: "commit", then: { row: 1, col: "rate" } });
    expect(act({ key: "Tab", editable: { rate: false, disc: true } })).toEqual({ type: "commit", then: { row: 1, col: "disc" } });
    expect(act({ key: "Enter", editable: both })).toEqual({ type: "commit", then: { row: 3, col: "item" } });
  });
  it("rate -> disc, or entry row when disc not editable", () => {
    expect(act({ col: "rate", key: "Enter" })).toEqual({ type: "commit", then: { row: 1, col: "disc" } });
    expect(act({ col: "rate", key: "Enter", editable: { rate: true, disc: false } })).toEqual({ type: "commit", then: { row: 3, col: "item" } });
  });
  it("disc -> entry row item (entry at top too)", () => {
    expect(act({ col: "disc", key: "Tab" })).toEqual({ type: "commit", then: { row: 3, col: "item" } });
    expect(act({ col: "disc", key: "Enter", row: 2, entryRow: 0 })).toEqual({ type: "commit", then: { row: 0, col: "item" } });
  });
  it("Shift+Tab walks back and skips non-editable", () => {
    const s = { key: "Tab", shift: true };
    expect(act({ ...s, col: "disc" })).toEqual({ type: "focus", row: 1, col: "rate" });
    expect(act({ ...s, col: "rate" })).toEqual({ type: "focus", row: 1, col: "qty" });
    expect(act({ ...s, col: "qty" })).toEqual({ type: "focus", row: 1, col: "item" });
    expect(act({ ...s, col: "disc", editable: { rate: false, disc: true } })).toEqual({ type: "focus", row: 1, col: "qty" });
  });
  it("Shift+Tab from item: row 0 none; else previous row's last editable", () => {
    const s = { key: "Tab", shift: true, col: "item" as const };
    expect(act({ ...s, row: 0 })).toEqual({ type: "none" });
    expect(act({ ...s, row: 2 })).toEqual({ type: "focus", row: 1, col: "disc" });
    expect(act({ ...s, row: 2, editable: { rate: true, disc: false } })).toEqual({ type: "focus", row: 1, col: "rate" });
    expect(act({ ...s, row: 2, editable: both })).toEqual({ type: "focus", row: 1, col: "qty" });
  });
  it("arrows closed: same column, clamped; number cells commit", () => {
    expect(act({ key: "ArrowUp" })).toEqual({ type: "commit", then: { row: 0, col: "qty" } });
    expect(act({ key: "ArrowDown", col: "item", row: 1 })).toEqual({ type: "focus", row: 2, col: "item" });
    expect(act({ key: "ArrowUp", col: "item", row: 0 })).toEqual({ type: "none" });
    expect(act({ key: "ArrowDown", col: "item", row: 3 })).toEqual({ type: "none" });
    expect(act({ key: "ArrowDown", row: 2 })).toEqual({ type: "commit", then: { row: 3, col: "qty" } });
  });
  it("Esc: closes dropdown, deletes empty line, none on entry row", () => {
    expect(act({ key: "Escape", col: "item", row: 3, dropdownOpen: true })).toEqual({ type: "closeDropdown" });
    expect(act({ key: "Escape", rowEmpty: true })).toEqual({ type: "deleteRow", row: 1, then: { row: 1, col: "qty" } });
    expect(act({ key: "Escape", row: 3, col: "item", rowEmpty: true })).toEqual({ type: "none" });
    expect(act({ key: "Escape" })).toEqual({ type: "none" });
  });
  it("Shift+Delete deletes non-entry row; next row or entry row", () => {
    expect(act({ key: "Delete", shift: true })).toEqual({ type: "deleteRow", row: 1, then: { row: 1, col: "qty" } });
    expect(act({ key: "Delete", shift: true, row: 1, col: "rate" })).toEqual({ type: "deleteRow", row: 1, then: { row: 1, col: "rate" } });
    // last line, entry at Bottom: entry row's post-deletion index is row
    expect(act({ key: "Delete", shift: true, row: 2, col: "rate" })).toEqual({ type: "deleteRow", row: 2, then: { row: 2, col: "item" } });
    expect(act({ key: "Delete", shift: true, row: 3, col: "item" })).toEqual({ type: "none" });
  });
  it("Shift+Delete on last line with entry at top lands on the entry row", () => {
    expect(act({ key: "Delete", shift: true, row: 3, entryRow: 0 })).toEqual({ type: "deleteRow", row: 3, then: { row: 0, col: "item" } });
  });
  it("delete edge cases: single line at Bottom, middle line with entry at Top", () => {
    expect(act({ key: "Delete", shift: true, row: 0, rowCount: 2, entryRow: 1 })).toEqual({ type: "deleteRow", row: 0, then: { row: 0, col: "item" } });
    expect(act({ key: "Escape", rowEmpty: true, row: 0, rowCount: 2, entryRow: 1, col: "qty" })).toEqual({ type: "deleteRow", row: 0, then: { row: 0, col: "item" } });
    expect(act({ key: "Delete", shift: true, row: 2, rowCount: 4, entryRow: 0, col: "disc" })).toEqual({ type: "deleteRow", row: 2, then: { row: 2, col: "disc" } });
  });
  it("Alt+D opens description on non-entry rows only", () => {
    expect(act({ key: "d", alt: true })).toEqual({ type: "openDescription", row: 1 });
    expect(act({ key: "D", alt: true })).toEqual({ type: "openDescription", row: 1 });
    expect(act({ key: "d", alt: true, row: 3, col: "item" })).toEqual({ type: "none" });
  });
  it("any other key is none", () => {
    expect(act({ key: "a" })).toEqual({ type: "none" });
  });
});

describe("qtyCommit", () => {
  it("a typed 0 removes the line; a new positive qty sets it; anything else is ignored", () => {
    expect(qtyCommit(0, 3)).toBe("remove");
    expect(qtyCommit(5, 3)).toBe(5);
    expect(qtyCommit(3, 3)).toBeNull();
    expect(qtyCommit(-2, 3)).toBeNull();
    expect(qtyCommit(undefined, 3)).toBeNull();
    expect(qtyCommit(NaN, 3)).toBeNull();
  });
});

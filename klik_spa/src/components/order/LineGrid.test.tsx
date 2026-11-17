import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { CartItem } from "../../../types";
import type { POSDetails } from "../../stores/posProfileStore";
import LineGrid, { ItemResults } from "./LineGrid";
import type { MenuItem } from "../../../types";
import { formatCurrencyWithSymbol } from "../../utils/currency";

const screen = vi.hoisted(() => ({ compact: false }));
vi.mock("../../hooks/useMediaQuery", () => ({ useMediaQuery: () => screen.compact }));

vi.mock("react-toastify", () => ({ toast: { error: vi.fn(), warning: vi.fn() } }));
vi.mock("../../utils/lineGridSearch", async (importOriginal) => ({
  highlightRanges: (await importOriginal<typeof import("../../utils/lineGridSearch")>()).highlightRanges,
  latestOnly: () => (p: Promise<unknown>) => p,
  STALE: Symbol("stale"),
  searchGridItems: vi.fn(async () => []),
  scanAdd: vi.fn(),
}));

const line = (id: string, name: string, description?: string) =>
  ({ id, item_code: id, name, category: "", price: 100, image: "", quantity: 1, description }) as CartItem;

const render = (items: CartItem[], pos: Partial<POSDetails> = {}, itemDiscounts = {}) =>
  renderToStaticMarkup(
    <LineGrid
      items={items}
      itemDiscounts={itemDiscounts}
      selectedId={null}
      onSelect={() => {}}
      onUpdateQuantity={() => {}}
      onCustomRateChange={() => {}}
      onDiscountChange={() => {}}
      onRemoveItem={() => {}}
      onUOMChange={() => {}}
      onOpenDescription={() => {}}
      onPickItem={async () => null}
      currencySymbol="KES"
      posDetails={{ name: "P", allow_rate_change: 1, allow_discount_change: 1, ...pos } as POSDetails}
    />,
  );

describe("LineGrid", () => {
  it("renders Item, Qty, UOM, Rate, Disc %, Total headers", () => {
    const html = render([line("A", "Apple")]);
    for (const h of ["Item", "Qty", "UOM", "Rate", "Disc %", "Total"]) expect(html).toContain(`>${h}</th>`);
  });

  it("shows the line's unit in the UOM column (a picker appears once the item has more than one)", () => {
    const html = render([{ ...line("A", "Apple"), uom: "Box" }]);
    expect(html).toMatch(/data-uom-cell="A"[^>]*>[^<]*<span[^>]*>Box<\/span>/);
  });

  it("chips a line's Loss of Sale left of its qty, when the till records LoS", () => {
    const los = { ...line("A", "Apple"), quantity: 4, los_qty: 6 } as CartItem;
    expect(render([los], { custom_enable_loss_of_sale: 1 })).toMatch(/data-los-chip="A"[^>]*>LoS 6<\/span>.*data-col="qty"/);
    expect(render([los])).not.toContain("data-los-chip");
    expect(render([line("A", "Apple")], { custom_enable_loss_of_sale: 1 })).not.toContain("data-los-chip");
  });

  it("under 1024 px, drops Disc % and the remove button and chips LoS under the name", () => {
    const los = { ...line("A", "Apple"), quantity: 4, los_qty: 6 } as CartItem;
    screen.compact = true;
    try {
      const html = render([los], { custom_enable_loss_of_sale: 1 });
      expect(html).not.toContain(">Disc %</th>");
      expect(html).not.toContain('aria-label="Remove line"');
      expect(html).toContain('colSpan="5"');
      expect(html).toMatch(/Apple<\/div><div class="xl:hidden"><span[^>]*>LoS 6<\/span>/);
    } finally {
      screen.compact = false;
    }
    expect(render([los], { custom_enable_loss_of_sale: 1 })).toContain(">Disc %</th>");
  });

  it("adds a Code column in plain text when the till shows item codes, and widens the entry row to match", () => {
    const off = render([line("SKU-1", "Apple")]);
    expect(off).not.toContain(">Code</th>");
    expect(off).toContain('colSpan="7"');
    const on = render([line("SKU-1", "Apple")], { custom_show_item_code_in_product_list: 1 });
    expect(on).toContain(">Code</th>");
    expect(on.indexOf(">Code</th>")).toBeLessThan(on.indexOf(">Item</th>"));
    expect(on.indexOf(">SKU-1</td>")).toBeLessThan(on.indexOf(">Apple</div>"));
    expect(on).toMatch(/<td[^>]*text-gray-900[^>]*>SKU-1<\/td>/);
    expect(on).toContain('colSpan="8"');
  });

  it("shows a description as an italic second line, and nothing when there is none", () => {
    expect(render([line("A", "Apple", "Green ones")])).toMatch(/<[^>]*class="[^"]*italic[^"]*"[^>]*>Green ones</);
    expect(render([line("A", "Apple")])).not.toContain("italic");
  });

  it("puts the entry row first for Top and last for Bottom", () => {
    const items = [line("A", "Apple"), line("B", "Banana")];
    const top = render(items, { custom_cart_item_insertion_position: "Top" });
    expect(top.indexOf("data-entry-row")).toBeLessThan(top.indexOf("Apple"));
    const bottom = render(items, { custom_cart_item_insertion_position: "Bottom" });
    expect(bottom.indexOf("data-entry-row")).toBeGreaterThan(bottom.indexOf("Banana"));
  });

  it("makes Rate and Disc readOnly when the till forbids changing them", () => {
    const cell = (html: string, col: string) => html.match(new RegExp(`<input[^>]*data-col="${col}"[^>]*>`))![0];
    const locked = render([line("A", "Apple")], { allow_rate_change: 0, allow_discount_change: 0 });
    expect(cell(locked, "rate")).toContain("readOnly");
    expect(cell(locked, "disc")).toContain("readOnly");
    const open = render([line("A", "Apple")]);
    expect(cell(open, "rate")).not.toContain("readOnly");
    expect(cell(open, "disc")).not.toContain("readOnly");
  });

  it("a custom-rate line shows the pre-discount rate, the % of it, and the discounted total", () => {
    const html = render([line("A", "Apple")], {}, { A: { customRate: 80, discountAmount: 4 } });
    const val = (col: string) => html.match(new RegExp(`<input[^>]*data-col="${col}"[^>]*value="([^"]*)"`))?.[1]
      ?? html.match(new RegExp(`<input[^>]*value="([^"]*)"[^>]*data-col="${col}"`))![1];
    expect(val("rate")).toBe("80.00");
    expect(val("disc")).toBe("5.00");
    expect(html).toContain("76.00");
  });
});

describe("ItemResults (the entry row's search dropdown)", () => {
  const pen = { id: "PEN", item_code: "PEN", name: "Blue Pen", price: 120, available: 5, cost_price: 80 } as MenuItem;
  const html = (showCost: boolean, results = [pen], showCode = false) =>
    renderToStaticMarkup(
      <ItemResults results={results} highlight={0} query="pen" currencySymbol="KES" showCost={showCost} showCode={showCode} onPick={() => {}} onHover={() => {}} />,
    );

  it("puts cost before the selling price", () => {
    const out = html(true);
    expect(out.indexOf(">Cost</span>")).toBeLessThan(out.indexOf(">Price</span>"));
    expect(out.indexOf(formatCurrencyWithSymbol(80, "KES"))).toBeLessThan(out.indexOf(formatCurrencyWithSymbol(120, "KES")));
  });

  it("shows the item code only when the profile shows codes in the product list", () => {
    expect(html(false, [pen], true)).toContain(">Code</span>");
    expect(html(false, [pen], true)).toMatch(/text-gray-900[^>]*>PEN<\/span>/);
    const out = html(false, [pen], true);
    expect(out.indexOf(">Code</span>")).toBeLessThan(out.indexOf(">Item</span>"));
    expect(out.indexOf(">PEN</span>")).toBeLessThan(out.indexOf("Blue"));
    expect(html(false, [pen], false)).not.toContain(">PEN</span>");
  });

  it("shows a Cost column only when the till shows cost", () => {
    expect(html(true)).toContain(">Cost</span>");
    expect(html(true)).toContain(formatCurrencyWithSymbol(80, "KES"));
    expect(html(false)).not.toContain(">Cost</span>");
    expect(html(false)).not.toContain(formatCurrencyWithSymbol(80, "KES"));
  });

  it("shows the unit the line is added in after the stock", () => {
    const out = html(false, [{ ...pen, uom: "Box" }]);
    expect(out.indexOf(">Stock</span>")).toBeLessThan(out.indexOf(">UOM</span>"));
    expect(out).toMatch(/>5<\/span><span[^>]*>Box<\/span>/);
  });

  it("shows ten rows and scrolls the rest", () => {
    const many = Array.from({ length: 20 }, (_, i) => ({ ...pen, id: `P${i}`, item_code: `P${i}` }));
    const out = html(false, many);
    expect(out.match(/role="option"/g)).toHaveLength(20);
    expect(out).toMatch(/role="listbox"[^>]*class="[^"]*max-h-\[17\.5rem\][^"]*overflow-y-auto/);
  });
});

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { ReturnLinks } from "./ReturnLinks";

const html = (inv: Parameters<typeof ReturnLinks>[0]["invoice"]) =>
  renderToStaticMarkup(<MemoryRouter><ReturnLinks invoice={inv} /></MemoryRouter>);

describe("ReturnLinks", () => {
  it("a return links to the sale it reverses", () => {
    const out = html({ is_return: 1, return_against: "TW/POS/00013" });
    expect(out).toContain("Return of");
    expect(out).toContain('href="/invoice/TW/POS/00013"');
  });

  it("a sale with credit notes links to each of them", () => {
    const out = html({ returns: [{ name: "POS-RET-1" }, { name: "POS-RET-2" }] });
    expect(out).toContain("Returned by");
    expect(out).toContain('href="/invoice/POS-RET-1"');
    expect(out).toContain('href="/invoice/POS-RET-2"');
  });

  it("a plain sale shows nothing", () => {
    expect(html({ returns: [] })).toBe("");
  });
});

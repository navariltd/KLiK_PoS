import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { CartItem } from "../../../types";
import { CartItemRow } from "./CartItemRow";

vi.mock("react-toastify", () => ({ toast: { error: vi.fn(), warning: vi.fn() } }));

const item = { id: "A", item_code: "A", name: "Apple", category: "", price: 100, image: "", quantity: 1 } as CartItem;

describe("grid till side panel (facts variant)", () => {
  it("keeps the pen that edits the line's notes, beside the eye", () => {
    const html = renderToStaticMarkup(
      <CartItemRow item={item} itemId="A" itemDiscount={{}} isExpanded onToggleExpand={() => {}} variant="facts" onDescriptionChange={() => {}} />,
    );
    expect(html).toContain('aria-label="View full details"');
    expect(html).toContain('aria-label="Edit description"');
  });
});

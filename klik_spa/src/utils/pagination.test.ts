import { describe, expect, it } from "vitest";
import { resolveNextOffset, shouldKeepPaginating } from "./pagination";

describe("resolveNextOffset", () => {
  it("uses the server cursor when it sends one", () => {
    expect(resolveNextOffset(250, 12)).toBe(250);
  });

  it("keeps a zero cursor instead of falling back", () => {
    // The bug this guards: `||` would treat 0 as absent and silently use the item count.
    expect(resolveNextOffset(0, 12)).toBe(0);
  });

  it("falls back when the backend predates next_offset", () => {
    expect(resolveNextOffset(undefined, 12)).toBe(12);
    expect(resolveNextOffset(null, 12)).toBe(12);
  });

  it("falls back on a nonsense cursor rather than trusting it", () => {
    expect(resolveNextOffset(Number.NaN, 12)).toBe(12);
    expect(resolveNextOffset(-1, 12)).toBe(12);
  });
});

describe("shouldKeepPaginating", () => {
  it("continues while the cursor is advancing and more remain", () => {
    expect(shouldKeepPaginating(true, 250, 500)).toBe(true);
  });

  it("stops once the server says there is no more", () => {
    expect(shouldKeepPaginating(false, 250, 500)).toBe(false);
  });

  it("stops when a page made no progress even though the server says there is more", () => {
    // The production loop: every item on the page was filtered out, the cursor stayed at 0,
    // and has_more stayed true, so the scroll sentinel re-fired forever.
    expect(shouldKeepPaginating(true, 0, 0)).toBe(false);
  });

  it("stops if the cursor ever goes backwards", () => {
    expect(shouldKeepPaginating(true, 500, 250)).toBe(false);
  });
});

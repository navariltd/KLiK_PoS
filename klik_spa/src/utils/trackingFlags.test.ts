import { describe, expect, it } from "vitest";
import { isExplicitlyNotTracked } from "./trackingFlags";

describe("isExplicitlyNotTracked", () => {
  it("treats an explicit 0 as definitely not tracked", () => {
    expect(isExplicitlyNotTracked(0)).toBe(true);
  });

  it("treats an explicit false as definitely not tracked", () => {
    expect(isExplicitlyNotTracked(false)).toBe(true);
  });

  it("treats an explicit 1 as tracked", () => {
    expect(isExplicitlyNotTracked(1)).toBe(false);
  });

  it("treats an explicit true as tracked", () => {
    expect(isExplicitlyNotTracked(true)).toBe(false);
  });

  it("treats undefined as unknown, not as not-tracked", () => {
    // This is the barcode-scan case: fetchItemByIdentifier never sets has_batch_no /
    // has_serial_no, so the flag arrives as undefined rather than false. Undefined must
    // NOT be treated as falsy here, or scanned items silently lose their batch/serial
    // fetch even when they are genuinely tracked.
    expect(isExplicitlyNotTracked(undefined)).toBe(false);
  });
});

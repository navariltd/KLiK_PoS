import { describe, expect, it } from "vitest";
import { UNKNOWN_AVAILABILITY, formatAvailability } from "./availability";

describe("formatAvailability", () => {
  it("shows the number when stock is readable", () => {
    expect(formatAvailability(7, false)).toBe("7");
    expect(formatAvailability(0, false)).toBe("0");
  });

  it("shows unknown rather than a confident zero when stock could not be read", () => {
    // The whole point: a permission gap must not read as an empty shelf.
    expect(formatAvailability(0, true)).toBe(UNKNOWN_AVAILABILITY);
    expect(formatAvailability(7, true)).toBe(UNKNOWN_AVAILABILITY);
  });

  it("treats a missing figure as unknown", () => {
    expect(formatAvailability(null, false)).toBe(UNKNOWN_AVAILABILITY);
    expect(formatAvailability(undefined, false)).toBe(UNKNOWN_AVAILABILITY);
    expect(formatAvailability("", false)).toBe(UNKNOWN_AVAILABILITY);
  });

  it("passes through a non-numeric label untouched", () => {
    expect(formatAvailability("Service", false)).toBe("Service");
  });
});

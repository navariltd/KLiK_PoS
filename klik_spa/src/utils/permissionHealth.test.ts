import { describe, expect, it } from "vitest";
import {
  listDoctypes,
  listRemedyRoles,
  summarisePermissionHealth,
  type PermissionGap,
} from "./permissionHealth";

const gap = (overrides: Partial<PermissionGap> = {}): PermissionGap => ({
  doctype: "Bin",
  permission: "read",
  severity: "degraded",
  consequence: "Stock levels are unknown.",
  granting_roles: ["Stock User"],
  ...overrides,
});

describe("listDoctypes", () => {
  it("reads naturally for one, two and three", () => {
    expect(listDoctypes([gap()])).toBe("Bin");
    expect(listDoctypes([gap(), gap({ doctype: "Item Price" })])).toBe("Bin and Item Price");
    expect(
      listDoctypes([gap(), gap({ doctype: "Item Price" }), gap({ doctype: "Warehouse" })]),
    ).toBe("Bin, Item Price and Warehouse");
  });

  it("is empty when nothing is missing", () => {
    expect(listDoctypes([])).toBe("");
  });
});

describe("listRemedyRoles", () => {
  it("de-duplicates a role that fixes more than one gap", () => {
    expect(
      listRemedyRoles([
        gap({ granting_roles: ["Stock User", "Stock Manager"] }),
        gap({ doctype: "Warehouse", granting_roles: ["Stock User"] }),
      ]),
    ).toEqual(["Stock Manager", "Stock User"]);
  });

  it("copes with a gap that names no role", () => {
    expect(listRemedyRoles([gap({ granting_roles: undefined })])).toEqual([]);
  });
});

describe("summarisePermissionHealth", () => {
  it("says nothing when healthy", () => {
    expect(summarisePermissionHealth({ healthy: true, missing: [], has_critical: false })).toBe("");
    expect(summarisePermissionHealth(null)).toBe("");
    expect(summarisePermissionHealth(undefined)).toBe("");
  });

  it("names the doctype, the consequence and the role that fixes it", () => {
    const text = summarisePermissionHealth({
      healthy: false,
      missing: [gap()],
      has_critical: false,
    });

    expect(text).toContain("cannot read Bin");
    expect(text).toContain("Stock levels are unknown.");
    expect(text).toContain("Stock User role");
  });

  it("leads differently when something critical is missing", () => {
    const text = summarisePermissionHealth({
      healthy: false,
      missing: [gap({ doctype: "Item", severity: "critical", consequence: "The catalogue cannot load." })],
      has_critical: true,
    });

    expect(text).toContain("missing access the POS needs");
  });

  it("falls back to a generic remedy when no role grants it", () => {
    const text = summarisePermissionHealth({
      healthy: false,
      missing: [gap({ granting_roles: [] })],
      has_critical: false,
    });

    expect(text).toContain("Ask an administrator");
  });

  it("pluralises the remedy across several roles", () => {
    const text = summarisePermissionHealth({
      healthy: false,
      missing: [gap({ granting_roles: ["Stock User", "Stock Manager"] })],
      has_critical: false,
    });

    expect(text).toContain("Stock Manager or Stock User roles");
  });
});

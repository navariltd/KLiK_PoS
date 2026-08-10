import { describe, expect, it } from "vitest";
import { resolveCompanyName } from "./companyName";

describe("resolveCompanyName", () => {
  it("returns a plain string company as-is", () => {
    expect(resolveCompanyName("Dev Co")).toBe("Dev Co");
  });

  it("reads name off a company object", () => {
    expect(resolveCompanyName({ name: "Dev Co", company_name: "Dev Company Ltd" })).toBe("Dev Co");
  });

  it("prefers name over company_name, because name is the docname the API expects", () => {
    expect(resolveCompanyName({ name: "DC", company_name: "Dev Company Ltd" })).toBe("DC");
  });

  it("falls back to company_name when name is absent", () => {
    expect(resolveCompanyName({ company_name: "Dev Company Ltd" })).toBe("Dev Company Ltd");
  });

  it("returns an empty string for undefined, null or an empty object", () => {
    expect(resolveCompanyName(undefined)).toBe("");
    expect(resolveCompanyName(null)).toBe("");
    expect(resolveCompanyName({})).toBe("");
  });

  it("trims surrounding whitespace", () => {
    expect(resolveCompanyName("  Dev Co  ")).toBe("Dev Co");
  });
});

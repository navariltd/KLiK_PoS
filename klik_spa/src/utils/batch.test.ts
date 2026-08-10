import { describe, expect, it, vi, afterEach } from "vitest";
import { getBatches } from "./batch";

function mockFetchOnce(response: { ok: boolean; status: number; json: () => Promise<unknown> }) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

describe("getBatches", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the batch array from a 200 response", async () => {
    mockFetchOnce({
      ok: true,
      status: 200,
      json: async () => ({ message: [{ batch_no: "B-001", qty: 5 }] }),
    });

    const batches = await getBatches("ITEM-001");
    expect(batches).toEqual([{ batch_no: "B-001", qty: 5 }]);
  });

  it("throws 'Invalid response format' for a malformed 200 body, and only then", async () => {
    mockFetchOnce({
      ok: true,
      status: 200,
      json: async () => ({ message: "not-an-array" }),
    });

    await expect(getBatches("ITEM-001")).rejects.toThrow("Invalid response format");
  });

  it("names the status and permission problem on a 403, instead of claiming bad format", async () => {
    mockFetchOnce({
      ok: false,
      status: 403,
      json: async () => ({
        exception: "frappe.exceptions.PermissionError: Insufficient Permission for Batch",
      }),
    });

    await expect(getBatches("_Test Non Stock Item")).rejects.toThrow(/403/);
    await expect(getBatches("_Test Non Stock Item")).rejects.toThrow(/[Pp]ermission/);

    // The old bug: a 403 must never surface as a claim about response "format".
    await expect(getBatches("_Test Non Stock Item")).rejects.not.toThrow("Invalid response format");
  });

  it("still names the status on a 403 with no server-supplied message", async () => {
    mockFetchOnce({
      ok: false,
      status: 403,
      json: async () => ({}),
    });

    await expect(getBatches("ITEM-001")).rejects.toThrow(/403/);
  });
});

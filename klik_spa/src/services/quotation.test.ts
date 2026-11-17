import { afterEach, describe, expect, it, vi } from "vitest";
import { createQuote, getQuoteDetails, getQuoteEmails, getQuotes, sendQuoteEmail } from "./quotation";

const reply = (message: unknown, ok = true) =>
  vi.fn().mockResolvedValue({ ok, status: ok ? 200 : 500, json: async () => ({ message }) });

afterEach(() => vi.unstubAllGlobals());

describe("createQuote", () => {
  it("posts the cart as data and returns the quote name", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    const fetchMock = reply({ success: true, quote_name: "SAL-QTN-0001" });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createQuote({ customer: { id: "C1" }, items: [] });

    expect(result).toEqual({ success: true, quote_name: "SAL-QTN-0001" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/method/klik_pos.api.quotation.create_quote");
    expect(init.method).toBe("POST");
    expect(init.headers["X-Frappe-CSRF-Token"]).toBe("t");
    expect(JSON.parse(init.body)).toEqual({ data: { customer: { id: "C1" }, items: [] } });
  });

  it("returns the server's refusal as a failure, not a throw", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    vi.stubGlobal("fetch", reply({ success: false, message: "Below the minimum selling price" }));

    expect(await createQuote({})).toEqual({ success: false, message: "Below the minimum selling price" });
  });

  it("reads a thrown server error from _server_messages", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    const msg = JSON.stringify({ message: "Not permitted" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({ _server_messages: JSON.stringify([msg]) }) }),
    );

    expect(await createQuote({})).toEqual({ success: false, message: "Not permitted" });
  });

  it("reports a network failure as a failure", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    expect(await createQuote({})).toEqual({ success: false, message: "offline" });
  });
});

describe("quote lookups", () => {
  it("getQuotes asks for the state the tab is on", async () => {
    const fetchMock = reply({ success: true, data: [] });
    vi.stubGlobal("fetch", fetchMock);
    await getQuotes({ state: "converted" });
    expect(String(fetchMock.mock.calls[0]![0])).toContain("state=converted");
  });

  it("getQuotes passes paging and search", async () => {
    const fetchMock = reply({ success: true, data: [{ name: "SAL-QTN-0001" }] });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getQuotes({ limit: 20, start: 40, search: "acme" });

    expect(result.data).toEqual([{ name: "SAL-QTN-0001" }]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/method/klik_pos.api.quotation.get_quotes?limit=20&start=40&search=acme&state=open",
      { credentials: "include" },
    );
  });

  it("getQuoteDetails asks for the quote by id", async () => {
    const fetchMock = reply({ success: true, name: "SAL-QTN-0001", expired: true, items: [] });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getQuoteDetails("SAL-QTN-0001");

    expect(result.expired).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/method/klik_pos.api.quotation.get_quote_details?quote_id=SAL-QTN-0001",
      { credentials: "include" },
    );
  });

  it("a refused lookup is a failure with the server's reason, not undefined", async () => {
    const msg = JSON.stringify({ message: "No permission for Quotation" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({ _server_messages: JSON.stringify([msg]) }) }),
    );

    expect(await getQuoteDetails("SAL-QTN-0001")).toEqual({ success: false, message: "No permission for Quotation" });
    expect(await getQuotes()).toEqual({ success: false, message: "No permission for Quotation" });
  });
});

describe("quote email", () => {
  it("prefill is [] when the server refuses", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({}) }));
    expect(await getQuoteEmails("SAL-QTN-1")).toEqual([]);
  });

  it("send throws the server's reason", async () => {
    vi.stubGlobal("window", { csrf_token: "t" });
    const msg = JSON.stringify({ message: "Not an email address: x" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 417, json: async () => ({ _server_messages: JSON.stringify([msg]) }) }));
    await expect(sendQuoteEmail("SAL-QTN-1", "x")).rejects.toThrow("Not an email address: x");
  });
});

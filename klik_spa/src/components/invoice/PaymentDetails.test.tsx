import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PaymentDetails, detailFacts, type PaymentDetail } from "./PaymentDetails";

const base = { currency: "KES", postingDate: "2026-11-15", paidAmount: 400, outstanding: 0, status: "Paid", statusClass: "" };
const cheque: PaymentDetail = {
  source: "payment_entry", mode_of_payment: "Cheque", amount: 300, reference_no: "CHQ-7781", reference_date: "2026-11-20",
  posting_date: "2026-11-16", payment_entry: "ACC-PAY-0001", remarks: "Bank it on the 20th",
};
const mpesa: PaymentDetail = { source: "payment_entry", mode_of_payment: "Mpesa", amount: 100, reference_no: "TKX12AB", phone_number: "254700000901", payment_entry: "ACC-PAY-0002", posting_date: "2026-11-15" };
const cash: PaymentDetail = { source: "till", mode_of_payment: "Cash", amount: 400, reference_no: null, remarks: null };

describe("PaymentDetails", () => {
  it("shows a row per payment with its reference and the cheque date", () => {
    const html = renderToStaticMarkup(<PaymentDetails payments={[cheque, mpesa]} {...base} />);
    expect(html).toContain("CHQ-7781");
    expect(html).toContain("2026-11-20");
    expect(html).toContain("TKX12AB");
    expect(html.match(/<details/g)).toHaveLength(2);
  });

  it("opens onto what identifies the payment, and nothing that is not recorded", () => {
    expect(detailFacts(mpesa)).toEqual([["Payment Entry", "ACC-PAY-0002"], ["Phone", "254700000901"], ["Received", "2026-11-15"]]);
    expect(detailFacts(cheque).map(([k]) => k)).toEqual(["Payment Entry", "Cheque/Ref. date", "Received", "Remarks"]);
    expect(detailFacts(cash)).toEqual([]);
  });

  it("shows outstanding and the due date only while something is owed", () => {
    expect(renderToStaticMarkup(<PaymentDetails payments={[]} {...base} dueDate="2026-12-01" />)).not.toContain("2026-12-01");
    const owed = renderToStaticMarkup(<PaymentDetails payments={[]} {...base} outstanding={250} dueDate="2026-12-01" modeLabel="-" />);
    expect(owed).toContain("Outstanding");
    expect(owed).toContain("2026-12-01");
    expect(owed).toContain("No payments recorded.");
  });
});

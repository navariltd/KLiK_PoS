from frappe.tests.utils import FrappeTestCase

from klik_pos.api.receivables import _group_receivable_rows


def _invoice_row(party="ACME", voucher_no="INV-001", invoiced=1000.0, outstanding=1000.0, **kwargs):
	row = {
		"party": party,
		"customer_name": kwargs.get("customer_name", party),
		"customer_group": kwargs.get("customer_group", "All Customer Groups"),
		"voucher_type": "Sales Invoice",
		"voucher_no": voucher_no,
		"posting_date": kwargs.get("posting_date", "2026-07-01"),
		"due_date": kwargs.get("due_date", "2026-07-01"),
		"invoiced": invoiced,
		"paid": invoiced - outstanding,
		"outstanding": outstanding,
		"range0": kwargs.get("range0", 0.0),
		"range1": kwargs.get("range1", outstanding),
		"range2": kwargs.get("range2", 0.0),
		"range3": kwargs.get("range3", 0.0),
		"range4": kwargs.get("range4", 0.0),
		"range5": kwargs.get("range5", 0.0),
	}
	row.update({k: v for k, v in kwargs.items() if k in row})
	return row


def _payment_row(party="ACME", voucher_no="ACC-PAY-001", outstanding=-500.0, posting_date="2026-07-15"):
	return {
		"party": party,
		"customer_name": party,
		"customer_group": "All Customer Groups",
		"voucher_type": "Payment Entry",
		"voucher_no": voucher_no,
		"posting_date": posting_date,
		"due_date": None,
		"invoiced": 0.0,
		"paid": 0.0,
		"outstanding": outstanding,
		"range0": 0.0,
		"range1": 0.0,
		"range2": 0.0,
		"range3": 0.0,
		"range4": 0.0,
		"range5": 0.0,
	}


class TestGroupReceivableRows(FrappeTestCase):
	"""The By Customer tab must agree with the TRANSACTION HISTORY page.

	Both derive from ERPNext's AR report engine; this covers the grouping layered on top.
	"""

	def test_groups_one_entry_per_customer(self):
		rows = [
			_invoice_row(party="ACME", voucher_no="INV-001"),
			_invoice_row(party="ACME", voucher_no="INV-002"),
			_invoice_row(party="BETA", voucher_no="INV-003"),
		]
		result = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual(len(result), 2)
		acme = next(r for r in result if r["customer"] == "ACME")
		self.assertEqual(len(acme["invoices"]), 2)

	def test_nests_invoice_detail_with_derived_paid(self):
		rows = [_invoice_row(voucher_no="INV-00561", invoiced=10100.0, outstanding=8100.0)]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		[invoice] = acme["invoices"]
		self.assertEqual(invoice["name"], "INV-00561")
		self.assertEqual(invoice["grand_total"], 10100.0)
		self.assertEqual(invoice["paid"], 2000.0)
		self.assertEqual(invoice["outstanding"], 8100.0)

	def test_days_overdue_counts_from_due_date(self):
		rows = [_invoice_row(due_date="2026-07-29")]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual(acme["invoices"][0]["days_overdue"], 7)

	def test_days_overdue_never_negative(self):
		rows = [_invoice_row(due_date="2026-09-01")]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual(acme["invoices"][0]["days_overdue"], 0)

	def test_unallocated_advance_reported_separately(self):
		rows = [_invoice_row(invoiced=2000.0, outstanding=2000.0), _payment_row(outstanding=-6100.0)]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual(acme["unallocated_advance"], 6100.0)
		# The advance is already netted into outstanding by the report engine.
		self.assertEqual(acme["outstanding"], -4100.0)

	def test_payment_row_sets_last_payment_to_latest(self):
		rows = [
			_invoice_row(),
			_payment_row(voucher_no="ACC-PAY-001", outstanding=0.0, posting_date="2026-07-15"),
			_payment_row(voucher_no="ACC-PAY-002", outstanding=0.0, posting_date="2026-08-03"),
		]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual(str(acme["last_payment"]), "2026-08-03")

	def test_aging_buckets_collapse_report_ranges(self):
		rows = [
			_invoice_row(
				voucher_no="INV-001", invoiced=100.0, outstanding=100.0,
				range0=10.0, range1=20.0, range2=30.0, range3=40.0, range4=50.0, range5=60.0,
			)
		]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		# range0 is ERPNext's "<0" column: not yet due. It gets its own bucket rather
		# than inflating 0-30, which is range1 alone.
		self.assertEqual(acme["bucket_current"], 10.0)
		self.assertEqual(acme["bucket_0_30"], 20.0)
		self.assertEqual(acme["bucket_31_60"], 30.0)
		self.assertEqual(acme["bucket_61_90"], 40.0)
		self.assertEqual(acme["bucket_90_plus"], 110.0)

	def test_not_yet_due_money_stays_out_of_the_overdue_buckets(self):
		# A customer whose only invoice is not yet due must not read as 0-30 aged.
		rows = [
			_invoice_row(voucher_no="INV-001", invoiced=500.0, outstanding=500.0, range0=500.0, range1=0.0)
		]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual(acme["bucket_current"], 500.0)
		self.assertEqual(acme["bucket_0_30"], 0.0)

	def test_buckets_sum_to_outstanding(self):
		rows = [
			_invoice_row(
				voucher_no="INV-001", invoiced=210.0, outstanding=210.0,
				range0=10.0, range1=20.0, range2=30.0, range3=40.0, range4=50.0, range5=60.0,
			)
		]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		bucket_total = (
			acme["bucket_current"]
			+ acme["bucket_0_30"]
			+ acme["bucket_31_60"]
			+ acme["bucket_61_90"]
			+ acme["bucket_90_plus"]
		)
		self.assertEqual(bucket_total, acme["outstanding"])

	def test_skips_report_totals_row(self):
		rows = [_invoice_row(), {"party": None, "outstanding": 1000.0}]
		result = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual(len(result), 1)

	def test_excludes_settled_customers(self):
		rows = [_invoice_row(party="SETTLED", invoiced=500.0, outstanding=0.0, range1=0.0)]
		self.assertEqual(_group_receivable_rows(rows, "2026-08-05"), [])

	def test_settled_invoices_are_not_nested(self):
		rows = [
			_invoice_row(voucher_no="INV-OPEN", invoiced=500.0, outstanding=500.0),
			_invoice_row(voucher_no="INV-PAID", invoiced=500.0, outstanding=0.0, range1=0.0),
		]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual([i["name"] for i in acme["invoices"]], ["INV-OPEN"])

	def test_invoices_sorted_oldest_due_first(self):
		rows = [
			_invoice_row(voucher_no="INV-NEW", due_date="2026-08-01"),
			_invoice_row(voucher_no="INV-OLD", due_date="2026-06-01"),
		]
		[acme] = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual([i["name"] for i in acme["invoices"]], ["INV-OLD", "INV-NEW"])

	def test_customers_sorted_by_outstanding_descending(self):
		rows = [
			_invoice_row(party="SMALL", voucher_no="INV-001", invoiced=100.0, outstanding=100.0),
			_invoice_row(party="BIG", voucher_no="INV-002", invoiced=900.0, outstanding=900.0),
		]
		result = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual([r["customer"] for r in result], ["BIG", "SMALL"])

	def test_status_map_is_applied(self):
		rows = [_invoice_row(voucher_no="INV-001")]
		[acme] = _group_receivable_rows(rows, "2026-08-05", {"INV-001": "Overdue"})
		self.assertEqual(acme["invoices"][0]["status"], "Overdue")

	def test_customer_with_only_an_advance_is_kept(self):
		rows = [_payment_row(party="PREPAID", outstanding=-300.0)]
		[prepaid] = _group_receivable_rows(rows, "2026-08-05")
		self.assertEqual(prepaid["unallocated_advance"], 300.0)
		self.assertEqual(prepaid["invoices"], [])

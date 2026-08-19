"""The opening-entry payment summary must survive real sales data.

_merge_payment_entry_rows builds its rows by hand as plain dicts, but _build_payment_summary
read them with attribute access. That raised AttributeError on every call that had anything to
summarise - and went unnoticed because an empty result set makes the comprehension a no-op, so
the endpoint only broke once a till had actually taken money.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.payment import _build_payment_summary, _merge_payment_entry_rows


class TestBuildPaymentSummary(FrappeTestCase):
	def _modes(self, *pairs):
		return [frappe._dict(mode_of_payment=mode, opening_amount=amount) for mode, amount in pairs]

	def test_it_reads_rows_produced_by_the_merge_helper(self):
		"""The regression: these two functions must agree on the row type."""
		sales_data = _merge_payment_entry_rows(
			[{"mode_of_payment": "Cash", "total_amount": 1430, "transactions": 2}],
			[{"mode_of_payment": "M-Pesa", "total_amount": 500, "transactions": 1}],
		)

		summary = _build_payment_summary(self._modes(("Cash", 100), ("M-Pesa", 0)), sales_data)

		by_mode = {row["name"]: row for row in summary}
		self.assertEqual(by_mode["Cash"]["amount"], 1430)
		self.assertEqual(by_mode["Cash"]["transactions"], 2)
		self.assertEqual(by_mode["Cash"]["openingAmount"], 100)
		self.assertEqual(by_mode["M-Pesa"]["amount"], 500)

	def test_a_mode_with_no_sales_reports_its_opening_float(self):
		summary = _build_payment_summary(self._modes(("Cash", 250)), [])

		self.assertEqual(summary[0]["openingAmount"], 250)
		self.assertEqual(summary[0]["amount"], 0.0)
		self.assertEqual(summary[0]["transactions"], 0)

	def test_it_tolerates_frappe_dict_rows_too(self):
		"""Query results arrive as _dict; hand-built rows as plain dicts. Both must work."""
		summary = _build_payment_summary(
			self._modes(("Cash", 0)),
			[frappe._dict(mode_of_payment="Cash", total_amount=42, transactions=3)],
		)

		self.assertEqual(summary[0]["amount"], 42)
		self.assertEqual(summary[0]["transactions"], 3)

	def test_a_row_without_a_mode_is_skipped_not_fatal(self):
		summary = _build_payment_summary(
			self._modes(("Cash", 0)),
			[
				{"total_amount": 99, "transactions": 1},
				{"mode_of_payment": "Cash", "total_amount": 5, "transactions": 1},
			],
		)

		self.assertEqual(summary[0]["amount"], 5)

	def test_sales_for_a_mode_absent_from_the_opening_entry_are_not_invented(self):
		summary = _build_payment_summary(
			self._modes(("Cash", 0)),
			[{"mode_of_payment": "Bank Draft", "total_amount": 700, "transactions": 1}],
		)

		self.assertEqual([row["name"] for row in summary], ["Cash"])
		self.assertEqual(summary[0]["amount"], 0.0)


class TestMergePaymentEntryRows(FrappeTestCase):
	def test_totals_from_both_sources_are_combined_per_mode(self):
		merged = _merge_payment_entry_rows(
			[{"mode_of_payment": "Cash", "total_amount": 100, "transactions": 1}],
			[{"mode_of_payment": "Cash", "total_amount": 50, "transactions": 2}],
		)

		self.assertEqual(len(merged), 1)
		self.assertEqual(merged[0]["total_amount"], 150)
		self.assertEqual(merged[0]["transactions"], 3)

	def test_rows_without_a_mode_are_dropped(self):
		self.assertEqual(_merge_payment_entry_rows([{"total_amount": 10}], []), [])

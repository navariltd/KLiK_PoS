from types import SimpleNamespace

from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_invoice import (
	_allocate_return_against_original,
	_compute_net_cash_held,
	_get_refundable_cash,
)


class TestComputeNetCashHeld(FrappeTestCase):
	"""Regression guards for the Jul 29 credit-note allocation fix.

	A return used to append a payment row for the full amount regardless of whether the customer
	had ever paid. On a POS invoice that nets the Debtors legs to zero, leaving the credit note
	with 0 outstanding: unallocated against the invoice and invisible to Payment Reconciliation.
	"""

	def test_credit_sale_refunds_nothing(self):
		# Unpaid credit sale: grand_total 300, all 300 still outstanding.
		self.assertEqual(_compute_net_cash_held(300, 300, 0, 0), 0)

	def test_fully_paid_sale_refunds_everything(self):
		self.assertEqual(_compute_net_cash_held(300, 0, 0, 0), 300)

	def test_partially_paid_sale_refunds_only_what_was_paid(self):
		# Paid 100 of 300.
		self.assertEqual(_compute_net_cash_held(300, 200, 0, 0), 100)

	def test_prior_cash_refund_reduces_the_cap(self):
		# Fully paid, then 100 already handed back by an earlier return.
		self.assertEqual(_compute_net_cash_held(300, 0, 0, 100), 200)

	def test_prior_credit_note_does_not_count_as_cash(self):
		# Credit sale where an earlier return already knocked 100 off the outstanding.
		# grand_total - outstanding would wrongly read 100 as "paid".
		self.assertEqual(_compute_net_cash_held(300, 200, 100, 0), 0)

	def test_mixed_prior_refund_and_credit(self):
		# Paid 100 of 300, then returned 150 as 100 cash + 50 credit.
		self.assertEqual(_compute_net_cash_held(300, 150, 50, 100), 0)

	def test_never_negative(self):
		self.assertEqual(_compute_net_cash_held(300, 0, 200, 200), 0)


class TestAllocateReturnAgainstOriginal(FrappeTestCase):
	@staticmethod
	def _return_doc(grand_total):
		return SimpleNamespace(
			grand_total=grand_total,
			rounded_total=0,
			update_outstanding_for_self=1,
			precision=lambda _fieldname: 2,
		)

	def test_unrefunded_credit_is_pushed_onto_the_original(self):
		# Credit sale return: nothing refunded, original still owes 300.
		return_doc = self._return_doc(-300)
		_allocate_return_against_original(return_doc, SimpleNamespace(outstanding_amount=300), 0)
		self.assertEqual(return_doc.update_outstanding_for_self, 0)

	def test_partial_cash_refund_credits_the_remainder(self):
		# Returned 300, only 100 was refundable cash; the other 200 fits the original's outstanding.
		return_doc = self._return_doc(-300)
		_allocate_return_against_original(return_doc, SimpleNamespace(outstanding_amount=200), 100)
		self.assertEqual(return_doc.update_outstanding_for_self, 0)

	def test_full_cash_refund_leaves_the_flag_alone(self):
		# Nothing left to allocate - the credit note is settled by the refund itself.
		return_doc = self._return_doc(-300)
		_allocate_return_against_original(return_doc, SimpleNamespace(outstanding_amount=0), 300)
		self.assertEqual(return_doc.update_outstanding_for_self, 1)

	def test_credit_larger_than_outstanding_stays_on_the_note(self):
		# Would drive the original negative, so keep the balance on the credit note where
		# Payment Reconciliation can see it.
		return_doc = self._return_doc(-300)
		_allocate_return_against_original(return_doc, SimpleNamespace(outstanding_amount=50), 0)
		self.assertEqual(return_doc.update_outstanding_for_self, 1)

	def test_non_pos_return_never_refunds_cash(self):
		# ERPNext's set_paid_amount clears `payments` on any non-POS return, so a payment row
		# there would be silently dropped and we would report a refund that never happened.
		original = SimpleNamespace(name="INV-0001", grand_total=300, outstanding_amount=0)
		self.assertEqual(_get_refundable_cash(original, SimpleNamespace(is_pos=0)), 0)

	def test_rounded_total_takes_precedence(self):
		return_doc = SimpleNamespace(
			grand_total=-299.6,
			rounded_total=-300,
			update_outstanding_for_self=1,
			precision=lambda _fieldname: 2,
		)
		_allocate_return_against_original(return_doc, SimpleNamespace(outstanding_amount=300), 0)
		self.assertEqual(return_doc.update_outstanding_for_self, 0)

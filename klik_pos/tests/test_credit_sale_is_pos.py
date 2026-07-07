from types import SimpleNamespace

from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_invoice import _is_pos_for_credit_sale


class TestIsPosForCreditSale(FrappeTestCase):
    def test_defaults_to_zero_when_flag_unset(self):
        # Regression guard for the Jul 6 fix (commit 633227a): credit sales must
        # stay is_pos=0 unless the POS Profile explicitly opts in.
        pos_profile = SimpleNamespace()
        self.assertEqual(_is_pos_for_credit_sale(pos_profile), 0)

    def test_zero_when_flag_explicitly_off(self):
        pos_profile = SimpleNamespace(custom_allow_credit_sales_as_pos=0)
        self.assertEqual(_is_pos_for_credit_sale(pos_profile), 0)

    def test_one_when_flag_enabled(self):
        pos_profile = SimpleNamespace(custom_allow_credit_sales_as_pos=1)
        self.assertEqual(_is_pos_for_credit_sale(pos_profile), 1)

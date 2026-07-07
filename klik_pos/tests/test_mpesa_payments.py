import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.mpesa import get_mpesa_payments


class TestGetMpesaPayments(FrappeTestCase):
	"""Regression coverage for the M-Pesa checkout search fix: the frontend
	called a whitelisted method that never existed
	(`frappe_mpsa_payments...mpesa_quick_pay.get_mpesa_payments`), so every
	search 404'd and the "M-Pesa Payment Options" modal always showed no
	results. `klik_pos.api.mpesa.get_mpesa_payments` replaces it.

	Fixtures are real documents (not mocks) so the actual `frappe.get_all`/
	`frappe.db.count` filters are exercised end to end.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = "_Test Company"
		# Unique shortcode per run so this test's rows are the only ones the
		# query can see, regardless of any other Mpesa Settings/C2B data on
		# the site.
		cls.shortcode = f"TSC{frappe.generate_hash(length=6).upper()}"
		cls.gateway_name = f"Test Mpesa Gateway {frappe.generate_hash(length=6)}"

		# Mpesa Settings.on_update() hard-commits real Payment Gateway/Account/
		# Mode of Payment records via an explicit frappe.db.commit() that this
		# test's rollback can't undo. db_insert() bypasses validate/on_update
		# entirely (raw INSERT), so the fixture stays transaction-safe.
		settings = frappe.get_doc(
			{
				"doctype": "Mpesa Settings",
				"payment_gateway_name": cls.gateway_name,
				"company": cls.company,
				"business_shortcode": cls.shortcode,
			}
		)
		settings.db_insert()

		cls.match_row = cls._make_c2b_payment(
			firstname="Zawadi",
			lastname="Mwangi",
			billref=f"BILL{frappe.generate_hash(length=6).upper()}",
			msisdn="254700000001",
		)
		cls.other_row = cls._make_c2b_payment(
			firstname="Otieno",
			lastname="Kamau",
			billref=f"BILL{frappe.generate_hash(length=6).upper()}",
			msisdn="254700000002",
		)

	@classmethod
	def _make_c2b_payment(cls, firstname, lastname, billref, msisdn, amount=100):
		# full_name is a derived/read-only field recomputed from
		# firstname/lastname in before_insert -> set_missing_values(); it
		# can't be set directly on insert.
		doc = frappe.get_doc(
			{
				"doctype": "Mpesa C2B Payment Register",
				"businessshortcode": cls.shortcode,
				"transactiontype": "Pay Bill",
				"transid": f"TX{frappe.generate_hash(length=8).upper()}",
				"transtime": "120000",
				"transamount": amount,
				"billrefnumber": billref,
				"msisdn": msisdn,
				"firstname": firstname,
				"lastname": lastname,
				"posting_date": frappe.utils.nowdate(),
				"posting_time": frappe.utils.nowtime(),
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def test_full_pending_count_with_no_search(self):
		result = get_mpesa_payments(self.company)
		self.assertEqual(result["count"], 2)
		self.assertEqual(result["payments"], [])
		self.assertEqual(result["shortcodes"], [self.shortcode])

	def test_two_char_search_returns_no_payments_but_correct_count(self):
		result = get_mpesa_payments(self.company, search="ab")
		self.assertEqual(result["count"], 2)
		self.assertEqual(result["payments"], [])

	def test_three_char_search_matches_full_name_only(self):
		result = get_mpesa_payments(self.company, search="Zawadi")
		self.assertEqual(result["count"], 2)
		names = [p["name"] for p in result["payments"]]
		self.assertEqual(names, [self.match_row.name])

	def test_mode_of_payment_param_does_not_exclude_unset_rows(self):
		# Regression guard: a pending row's mode_of_payment is only populated
		# later via a separate Mpesa C2B Payment Register URL lookup, so it's
		# NULL here. Passing a truthy mode_of_payment must NOT filter it out
		# (that mistake was caught by review in the reverted first attempt).
		self.match_row.reload()
		self.assertFalse(self.match_row.mode_of_payment)

		result = get_mpesa_payments(
			self.company,
			mode_of_payment="Cash",
			search=self.match_row.billrefnumber,
		)
		names = [p["name"] for p in result["payments"]]
		self.assertIn(self.match_row.name, names)
